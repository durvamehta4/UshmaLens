"""Hourly physiology -> daily aggregates -> climatology -> Human Thermal Stress Index.

The central methodological rule, inherited from USHMA: every index is evaluated
at the native hourly resolution, where temperature, humidity, wind and radiation
belong to the same hour, and only then aggregated. Pairing a day's maximum
temperature with its mean humidity describes an hour that never happened (in the
Odisha audit it flagged 49.7% of summer days "Red").

One code path serves the live forecast, the ensemble, the historical replay and
the backtest, so there is no separate "live" implementation to drift.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ushmalens import config as C
from ushmalens.indices.heat_index import heat_index
from ushmalens.indices.psychro import relative_humidity, wind_at_height
from ushmalens.indices.solar import solar_geometry
from ushmalens.indices.utci import mean_radiant_temperature_human, utci
from ushmalens.indices.wbgt import wbgt_liljegren

PCTS = (50.0, 90.0, 95.0, 98.0)


# --------------------------------------------------------------------------- hourly
def hourly_indices(df: pd.DataFrame, lat: float, lon: float) -> pd.DataFrame:
    """Add rh, wbgt (Liljegren), tg, tnwb, tmrt (human), utci and heat_index.

    ``df`` needs ``ts_utc, t2m, dewpoint, pressure_kpa, wind10, ghi``. Rows may
    repeat timestamps (ensemble members); everything is vectorised.
    """
    out = df.copy()
    ta = out["t2m"].to_numpy(np.float64)
    td = np.minimum(out["dewpoint"].to_numpy(np.float64), ta)
    ws10 = out["wind10"].to_numpy(np.float64)
    ps = out["pressure_kpa"].to_numpy(np.float64)
    ghi = np.maximum(out["ghi"].to_numpy(np.float64), 0.0)

    sol = solar_geometry(out["ts_utc"], lat, lon, ghi)
    cz = sol["cos_zenith"].to_numpy(np.float64)
    fdir = sol["direct_fraction"].to_numpy(np.float64)
    rh = relative_humidity(ta, td)
    ws2 = wind_at_height(ws10)

    w = wbgt_liljegren(t_c=ta, dewpoint_c=td, wind_ms=ws2, pressure_kpa=ps,
                       solar_wm2=ghi, cos_zenith=cz, direct_fraction=fdir)
    # UTCI takes the radiant temperature of a standing person, not a black globe.
    tmrt = mean_radiant_temperature_human(ta, td, ghi, cz, fdir)
    out["rh"] = rh.astype(np.float32)
    out["wbgt"] = w["wbgt"].astype(np.float32)
    out["tg"] = w["tg"].astype(np.float32)
    out["tnwb"] = w["tnwb"].astype(np.float32)
    out["tmrt"] = tmrt.astype(np.float32)
    out["utci"] = utci(ta, tmrt, ws10, rh).astype(np.float32)
    out["heat_index"] = heat_index(ta, rh).astype(np.float32)
    out["ts_ist"] = pd.to_datetime(out["ts_utc"]) + pd.Timedelta(hours=C.IST_OFFSET_H)
    return out


# --------------------------------------------------------------------------- daily
def daily_aggregate(h: pd.DataFrame, key: str = "unit") -> pd.DataFrame:
    """Collapse hourly indices to one row per ``key`` per IST day.

    Night minima are assigned to the day the night *ends* on, so the 22:00
    reading belongs with the following morning's recovery period.
    """
    h = h.copy()
    ts = pd.DatetimeIndex(h["ts_ist"])
    h["date"] = ts.normalize()
    hour = ts.hour
    cols = ["t2m", "rh", "wbgt", "utci", "heat_index", "tmrt"]
    g = h.groupby([key, "date"])
    daily = g[cols].agg(["max", "mean", "min"])
    daily.columns = [f"{c}_{s}" for c, s in daily.columns]
    daily["n_hours"] = g.size()
    daily = daily.reset_index()

    night = h[(hour >= C.NIGHT_START_H) | (hour < C.NIGHT_END_H)].copy()
    nts = pd.DatetimeIndex(night["ts_ist"])
    night["date"] = np.where(nts.hour >= C.NIGHT_START_H, (nts + pd.Timedelta(days=1)).normalize(), nts.normalize())
    nightly = (night.groupby([key, "date"])[["t2m", "wbgt", "utci"]].min()
               .rename(columns=lambda c: f"{c}_night_min").reset_index())
    daily = daily.merge(nightly, on=[key, "date"], how="left")

    h["excess"] = np.maximum(h["wbgt"].to_numpy() - 28.0, 0.0)
    h["h30"] = (h["wbgt"] >= 30.0).astype(np.int16)
    h["h32"] = (h["wbgt"] >= 32.0).astype(np.int16)
    extra = h.groupby([key, "date"]).agg(wbgt_degree_hours=("excess", "sum"),
                                          wbgt_hours_30=("h30", "sum"), wbgt_hours_32=("h32", "sum"))
    daily = daily.merge(extra.reset_index(), on=[key, "date"], how="left")
    # Partial days at the edges of a window understate maxima; flag them.
    daily["complete"] = daily["n_hours"] >= 20
    return daily


# --------------------------------------------------------------------------- climatology
def doy_no_leap(dates) -> np.ndarray:
    ts = pd.DatetimeIndex(dates)
    doy = ts.dayofyear.to_numpy().copy()
    doy[np.asarray(ts.is_leap_year) & (doy > 59)] -= 1
    return doy


def build_climatology(daily: pd.DataFrame, cols=("wbgt_max", "t2m_max", "utci_max", "wbgt_night_min"),
                      window: int = 15) -> dict:
    """Per-day-of-year percentiles pooled over a +/-``window`` day window and all years."""
    d = daily[daily["complete"]].copy()
    doy = doy_no_leap(d["date"]) - 1
    years = pd.DatetimeIndex(d["date"]).year.to_numpy()
    ys = np.unique(years)
    yi = np.searchsorted(ys, years)
    out = {"years": [int(ys.min()), int(ys.max())], "window_days": window, "percentiles": list(PCTS)}
    offs = np.arange(-window, window + 1)
    for col in cols:
        cube = np.full((len(ys), 365), np.nan)
        cube[yi, doy] = d[col].to_numpy(np.float64)
        res = np.empty((365, len(PCTS)))
        for k in range(365):
            pooled = cube[:, (k + offs) % 365].ravel()
            res[k] = np.nanpercentile(pooled, PCTS)
        out[col] = {f"p{int(p)}": np.round(res[:, j], 2).tolist() for j, p in enumerate(PCTS)}
    # Monthly normal of daily-max air temperature: the IMD departure baseline.
    m = d.assign(month=pd.DatetimeIndex(d["date"]).month).groupby("month")["t2m_max"].mean()
    out["monthly_tmax_normal"] = {int(k): round(float(v), 2) for k, v in m.items()}
    return out


def pct_rank(values: np.ndarray, dates, clim: dict, col: str = "wbgt_max") -> np.ndarray:
    """Where each value sits in its own day-of-year distribution (0-100)."""
    v = np.asarray(values, dtype=np.float64)
    k = doy_no_leap(dates) - 1
    br = np.column_stack([np.asarray(clim[col][f"p{int(p)}"])[k] for p in PCTS])
    pa = np.asarray(PCTS)
    rank = np.full(v.shape, np.nan)
    below = v <= br[:, 0]
    rank[below] = pa[0] * v[below] / np.maximum(br[below, 0], 1e-6)
    for i in range(len(PCTS) - 1):
        lo, hi = br[:, i], br[:, i + 1]
        seg = (v > lo) & (v <= hi)
        rank[seg] = pa[i] + (pa[i + 1] - pa[i]) * (v[seg] - lo[seg]) / np.maximum(hi - lo, 1e-6)[seg]
    above = v > br[:, -1]
    span = np.maximum(br[:, -1] - br[:, -2], 1e-6)
    rank[above] = np.minimum(100.0, pa[-1] + (100 - pa[-1]) * (v[above] - br[above, -1]) / span[above])
    return np.clip(rank, 0, 100)


def p90(dates, clim: dict, col: str = "wbgt_max") -> np.ndarray:
    return np.asarray(clim[col]["p90"])[doy_no_leap(dates) - 1]


# --------------------------------------------------------------------------- HTSI
def scale_intensity(utci_max):
    """Daily-max UTCI on 0-100 through the official UTCI stress-band edges."""
    return np.clip(np.interp(utci_max, [9, 26, 32, 38, 46, 55], [0, 25, 45, 65, 85, 100]), 0, 100)


def scale_anomaly(rank):
    """Flat for ordinary days, steep through the tail where the epidemiology lives."""
    return np.clip(np.interp(rank, [0, 80, 90, 95, 98, 100], [0, 10, 40, 65, 85, 100]), 0, 100)


def scale_night(night_wbgt_min, night_t_min=None):
    """Overnight recovery deficit: 6 degC above the threshold = no recovery.

    Two routes, whichever is worse: a humid night (overnight WBGT above 26 degC,
    USHMA's channel) or a hot dry night (overnight air temperature above
    29 degC). The second was added for the dry-heat cities -- Ahmedabad and
    Hyderabad nights rarely reach 26 degC WBGT even in a lethal heatwave, yet
    30 degC nights are exactly when bodies fail to recover.
    """
    wet = np.clip(np.maximum(np.asarray(night_wbgt_min, dtype=float) - C.NIGHT_RELIEF_THRESHOLD_C, 0) / 6 * 100, 0, 100)
    if night_t_min is None:
        return wet
    dry = np.clip(np.maximum(np.asarray(night_t_min, dtype=float) - C.NIGHT_AIR_THRESHOLD_C, 0) / 6 * 100, 0, 100)
    return np.maximum(wet, dry)


def scale_duration(spell_days):
    x = np.clip(spell_days, 0, None) / max(C.DURATION_SATURATION_DAYS, 1)
    return np.clip(100 * (1 - np.exp(-1.6 * x)), 0, 100)


def spell_lengths(hot: np.ndarray) -> np.ndarray:
    out = np.zeros(len(hot), dtype=np.int32)
    run = 0
    for i, h in enumerate(hot):
        run = run + 1 if h else 0
        out[i] = run
    return out


def band_of(htsi):
    e1, e2, e3 = C.BAND_EDGES
    h = np.asarray(htsi, dtype=np.float64)
    idx = (h >= e1).astype(int) + (h >= e2) + (h >= e3)
    return np.asarray(C.BANDS, dtype=object)[idx]


def htsi_matrix(daily: pd.DataFrame, clim: dict, d_day=0.0, d_night=0.0, weights: dict | None = None) -> dict:
    """HTSI and its four channels for every ward that shares one weather unit.

    ``daily`` is that unit's daily frame; ``d_day`` / ``d_night`` are arrays of
    per-ward urban-heat-island uplifts (degC of air temperature). Returns
    ``dates`` plus 2-D arrays shaped (wards, days).

    The uplifts enter the *absolute* channels (intensity, night relief, the
    physiological gate); the *relative* channels (anomaly, duration) are judged
    on the unit's value against its own climatology, because people are
    acclimatised to their ward's usual island.
    """
    w = weights or C.HTSI_WEIGHTS
    tot = sum(w.values())
    w = {k: v / tot for k, v in w.items()}
    df = daily.sort_values("date").reset_index(drop=True)
    dates = df["date"]
    dd = np.atleast_1d(np.asarray(d_day, dtype=np.float64))[:, None]
    dn = np.atleast_1d(np.asarray(d_night, dtype=np.float64))[:, None]
    wbgt_max = df["wbgt_max"].to_numpy(np.float64)
    t_max = df["t2m_max"].to_numpy(np.float64)
    k = C.UHI["wbgt_per_degc"]
    # UTCI rises roughly 1:1 with air temperature; WBGT about 0.55:1.
    utci_w = df["utci_max"].to_numpy(np.float64)[None, :] + dd
    wbgt_w = wbgt_max[None, :] + k * dd
    night_w = np.nan_to_num(df["wbgt_night_min"].to_numpy(np.float64), nan=0.0)[None, :] + k * dn
    night_t = np.nan_to_num(df["t2m_night_min"].to_numpy(np.float64), nan=0.0)[None, :] + dn

    # Anomaly is judged on whichever regime is more unusual for this place and
    # time of year: humid heat (WBGT) or dry heat (air temperature, the
    # quantity IMD's departure rule uses). WBGT alone -- USHMA's Odisha choice --
    # ranked Hyderabad's lethal May 2015 dry heatwave as ordinary.
    rank_wet = pct_rank(wbgt_max, dates, clim, "wbgt_max")
    rank_dry = pct_rank(t_max, dates, clim, "t2m_max")
    rank = np.maximum(rank_wet, rank_dry)
    hot = (wbgt_max >= p90(dates, clim, "wbgt_max")) | (t_max >= p90(dates, clim, "t2m_max"))
    spell = spell_lengths(hot)
    lo, hi = C.HTSI_GATE_WBGT
    gate = np.clip((wbgt_w - lo) / (hi - lo), 0, 1)

    ch = {
        "intensity": scale_intensity(utci_w),
        "anomaly": scale_anomaly(rank)[None, :] * gate,
        "night_relief": scale_night(night_w, night_t),
        "duration": scale_duration(spell)[None, :] * gate,
    }
    htsi = sum(w[c] * ch[c] for c in ch)
    return {
        "dates": pd.DatetimeIndex(dates), "base": df, "htsi": htsi,
        "c_intensity": ch["intensity"], "c_anomaly": ch["anomaly"], "c_night_relief": ch["night_relief"],
        "c_duration": ch["duration"], "gate": gate,
        "wbgt_max_w": wbgt_w, "utci_max_w": utci_w, "wbgt_night_min_w": night_w, "t2m_night_min_w": night_t,
        "pct_rank": rank, "anomaly_regime": np.where(rank_dry > rank_wet, "dry", "humid"), "spell_days": spell,
    }


def htsi_series(daily: pd.DataFrame, clim: dict, d_day: float = 0.0, d_night: float = 0.0,
                weights: dict | None = None) -> pd.DataFrame:
    """Single-place convenience wrapper around :func:`htsi_matrix`."""
    m = htsi_matrix(daily, clim, [d_day], [d_night], weights)
    df = m["base"].copy()
    for c in ("htsi", "c_intensity", "c_anomaly", "c_night_relief", "c_duration", "gate",
              "wbgt_max_w", "utci_max_w", "wbgt_night_min_w", "t2m_night_min_w"):
        df[c] = m[c][0]
    for c in ("pct_rank", "anomaly_regime", "spell_days"):
        df[c] = m[c]
    df["band"] = band_of(df["htsi"])
    return df


def imd_heatwave(tmax: np.ndarray, normal: np.ndarray, coastal: bool) -> np.ndarray:
    """IMD's temperature-only heat-wave rule (the comparison baseline).

    Plains: Tmax >= 40 and departure >= 4.5 degC, or Tmax >= 45.
    Coastal: Tmax >= 37 and departure >= 4.5 degC.
    """
    tmax = np.asarray(tmax)
    dep = tmax - np.asarray(normal)
    if coastal:
        return (tmax >= 37) & (dep >= 4.5)
    return ((tmax >= 40) & (dep >= 4.5)) | (tmax >= 45)
