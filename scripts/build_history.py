"""Turn the ERA5 hourly record into everything the live system leans on.

For each city:
  1. hourly WBGT / UTCI / Heat Index through the same physics as live mode
  2. daily aggregates (IST days, night window 22:00-06:00)
  3. day-of-year climatology 2014-2025  -> data/build/climatology.json
  4. daily HTSI history                 -> data/build/history_<City>.parquet
  5. backtest vs the IMD temperature rule, and the city's worst heat episodes
                                        -> data/build/backtest.json

    python scripts/build_history.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ushmalens import config as C  # noqa: E402
from ushmalens import science as S  # noqa: E402
from ushmalens.cities import CITIES  # noqa: E402

CLIM_YEARS = (2014, 2025)
TARGET_QUANTILES = (0.90, 0.97, 0.99)


def episodes(h: pd.DataFrame, n: int = 4) -> list[dict]:
    """The city's worst multi-day heat episodes by summed HTSI above the Watch edge."""
    d = h.set_index("date")["htsi"]
    edge = C.BAND_EDGES[0]
    used = set()
    out = []
    # 7-day rolling excess over the Watch edge, centred.
    roll = (d - edge).clip(lower=0).rolling(7, center=True, min_periods=4).sum()
    for peak in roll.sort_values(ascending=False).index:
        if len(out) >= n:
            break
        if any(abs((peak - u).days) < 20 for u in used):
            continue
        if roll[peak] <= 0:
            break
        used.add(peak)
        win = d[peak - pd.Timedelta(days=10): peak + pd.Timedelta(days=10)]
        out.append({
            "peak": peak.date().isoformat(),
            "start": (peak - pd.Timedelta(days=12)).date().isoformat(),
            "end": (peak + pd.Timedelta(days=8)).date().isoformat(),
            "maxHtsi": round(float(win.max()), 1),
            "daysWarningPlus": int((win >= C.BAND_EDGES[1]).sum()),
        })
    return out


def main() -> int:
    from ushmalens.places import Places

    places = Places()
    clim_all, per_city = {}, {}
    # ---- pass 1: physics, climatology, ward-level HTSI over the whole record
    for city, cfg in CITIES.items():
        t0 = time.time()
        raw = pd.read_parquet(C.ERA5_DIR / f"{city}.parquet")
        raw["ts_utc"] = pd.to_datetime(raw["ts_utc"])
        lon, lat = cfg["centre"]
        hourly = S.hourly_indices(raw, lat, lon)
        hourly["unit"] = city
        daily = S.daily_aggregate(hourly)
        daily = daily[daily["complete"]].reset_index(drop=True)
        yrs = pd.DatetimeIndex(daily["date"]).year
        clim = S.build_climatology(daily[(yrs >= CLIM_YEARS[0]) & (yrs <= CLIM_YEARS[1])])
        clim_all[city] = clim
        wards = places.by_city[city]
        m = S.htsi_matrix(daily, clim, [w.d_day for w in wards], [w.d_night for w in wards])
        pop = np.array([w.population for w in wards])
        per_city[city] = {"daily": daily, "clim": clim, "m": m, "pop": pop}
        print(f"{city}: {len(daily):,} days, {len(wards)} wards, {time.time() - t0:.0f}s", flush=True)

    # ---- band edges: USHMA's rule (top ~10% / 3% / 1% of summer days), applied
    # to the pooled March-June *ward-day* distribution of these four cities.
    pooled = np.concatenate([pc["m"]["htsi"][:, np.isin(pc["m"]["dates"].month, [3, 4, 5, 6])].ravel()
                             for pc in per_city.values()])
    edges = [round(float(np.quantile(pooled, q)), 1) for q in TARGET_QUANTILES]
    calib = {"generated": time.strftime("%Y-%m-%d"), "bandEdges": edges,
             "rule": "pooled March-June ward-day HTSI quantiles " + str(TARGET_QUANTILES) + " over ERA5 2014-2026, all four cities",
             "ushmaOdishaEdges": [48.5, 59.5, 63.5]}
    (C.BUILD_DIR / "calibration.json").write_text(json.dumps(calib, indent=1))
    C.BAND_EDGES = tuple(edges)
    print("band edges:", edges, flush=True)

    # ---- pass 2: backtest on the population-weighted city HTSI
    backtest = {"generated": time.strftime("%Y-%m-%d"), "bandEdges": edges, "cities": {}}
    for city, cfg in CITIES.items():
        pc = per_city[city]
        m, daily, clim = pc["m"], pc["daily"], pc["clim"]
        h = S.htsi_series(daily, clim)                       # city centre, no UHI (for channels/IMD)
        h["htsi_centre"] = h["htsi"]
        h["htsi"] = (m["htsi"] * pc["pop"][:, None]).sum(axis=0) / pc["pop"].sum()
        h["band"] = S.band_of(h["htsi"])
        months = pd.DatetimeIndex(h["date"]).month
        normal = np.array([clim["monthly_tmax_normal"][mm] for mm in months])
        h["imd_hw"] = S.imd_heatwave(h["t2m_max"].to_numpy(), normal, cfg["coastal"])
        keep = ["date", "t2m_max", "t2m_min", "rh_mean", "wbgt_max", "wbgt_night_min", "utci_max", "heat_index_max",
                "wbgt_hours_30", "t2m_night_min", "pct_rank", "anomaly_regime", "spell_days", "gate", "c_intensity",
                "c_anomaly", "c_night_relief", "c_duration", "htsi_centre", "htsi", "band", "imd_hw"]
        h[keep].to_parquet(C.BUILD_DIR / f"history_{city}.parquet", index=False)

        warn = h["htsi"] >= edges[1]
        hw = h["imd_hw"]
        mam = months.isin([3, 4, 5, 6])
        wmat = m["htsi"]
        ward_share = (wmat[:, mam] >= edges[1]).mean(axis=1)
        by_month = h.assign(mo=months).groupby("mo").agg(
            warning=("htsi", lambda s_: int((s_ >= edges[1]).sum())), imd=("imd_hw", "sum"))
        backtest["cities"][city] = {
            "days": int(len(h)), "range": [h["date"].min().date().isoformat(), h["date"].max().date().isoformat()],
            "shareWatchPlus": round(float((h["htsi"] >= edges[0]).mean()), 4),
            "shareWarningPlus": round(float(warn.mean()), 4),
            "shareEmergency": round(float((h["htsi"] >= edges[2]).mean()), 4),
            "shareWarningPlusMarJun": round(float(warn[mam].mean()), 4),
            "wardShareWarningPlusMarJun": {"min": round(float(ward_share.min()), 4),
                                           "median": round(float(np.median(ward_share)), 4),
                                           "max": round(float(ward_share.max()), 4)},
            "imdHeatwaveDays": int(hw.sum()),
            "warningDays": int(warn.sum()),
            "warningMissedByImd": int((warn & ~hw).sum()),
            "imdNotWarning": int((hw & ~warn).sum()),
            "meanTmaxOnMissed": round(float(h.loc[warn & ~hw, "t2m_max"].mean()), 1) if (warn & ~hw).any() else None,
            "meanWbgtOnMissed": round(float(h.loc[warn & ~hw, "wbgt_max"].mean()), 1) if (warn & ~hw).any() else None,
            "humidShareOfWarning": round(float((h.loc[warn, "anomaly_regime"] == "humid").mean()), 3) if warn.any() else None,
            "byMonth": {int(mo): {"warning": int(r.warning), "imd": int(r.imd)} for mo, r in by_month.iterrows()},
            "htsiP90MarJun": round(float(h.loc[mam, "htsi"].quantile(0.9)), 1),
            "htsiP97": round(float(h["htsi"].quantile(0.97)), 1),
            "htsiP99MarJun": round(float(h.loc[mam, "htsi"].quantile(0.99)), 1),
            "episodes": episodes(h),
        }

    (C.BUILD_DIR / "climatology.json").write_text(json.dumps(clim_all))
    (C.BUILD_DIR / "backtest.json").write_text(json.dumps(backtest, indent=1))
    print(json.dumps({c: {k: v for k, v in b.items() if k not in ("byMonth",)} for c, b in backtest["cities"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
