"""Weather inputs: live NWP, the NWP ensemble, and ERA5 for replay.

Every source delivers the same five hourly variables the physics needs --
air temperature, dewpoint, surface pressure, 10 m wind and *measured-model*
shortwave radiation -- so no index is ever computed from an estimated input
(TAPAS had to estimate radiation from cloud cover; USHMA's Odisha files lacked a
live feed). All from Open-Meteo, which is free and keyless.

Provenance travels with every frame (``source`` and ``fetched_at``), failures
fall back down a labelled chain, and the last good response is cached on disk so
a rate limit or an outage degrades to "stale, N hours old" rather than to nothing.

    deterministic, per weather cell   api.open-meteo.com/v1/forecast       (best-match NWP)
    ensemble, per city centre         ensemble-api.open-meteo.com          (GFS 31 members)
    history / replay                  data/era5/<City>.parquet             (ERA5, prefetched)
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import httpx
import numpy as np
import pandas as pd

from ushmalens import config as C

FORECAST_API = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_API = "https://ensemble-api.open-meteo.com/v1/ensemble"
VARS = ["temperature_2m", "dew_point_2m", "surface_pressure", "wind_speed_10m", "shortwave_radiation"]
COLS = {"temperature_2m": "t2m", "dew_point_2m": "dewpoint", "surface_pressure": "pressure_kpa",
        "wind_speed_10m": "wind10", "shortwave_radiation": "ghi"}
CACHE = C.RUNTIME_DIR / "weather_cache"
CACHE.mkdir(parents=True, exist_ok=True)
STALE_MAX_H = 30
PAST_DAYS = 7
FORECAST_DAYS = 7


class WeatherUnavailable(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc)


def _get(url: str, params: dict, cache_name: str) -> tuple[list, dict]:
    """GET with retries; on failure fall back to the cached copy if fresh enough."""
    path = CACHE / f"{cache_name}.json"
    err = None
    try:
        with httpx.Client(timeout=90) as client:
            for attempt in range(3):
                r = client.get(url, params=params)
                if r.status_code == 429 and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    reason = ""
                    try:
                        reason = r.json().get("reason", "")
                    except ValueError:
                        pass
                    raise WeatherUnavailable(f"HTTP {r.status_code} {reason}".strip())
                data = r.json()
                data = data if isinstance(data, list) else [data]
                meta = {"fetched_at": _now().isoformat(timespec="seconds"), "stale": False}
                path.write_text(json.dumps({"meta": meta, "data": data}))
                return data, meta
    except (httpx.HTTPError, WeatherUnavailable, ValueError) as e:
        err = e
    if path.exists():
        blob = json.loads(path.read_text())
        age_h = (_now() - datetime.fromisoformat(blob["meta"]["fetched_at"])).total_seconds() / 3600
        if age_h <= STALE_MAX_H:
            meta = dict(blob["meta"], stale=True, age_hours=round(age_h, 1), error=str(err))
            return blob["data"], meta
    raise WeatherUnavailable(str(err))


def _frame(hourly: dict, suffix: str = "") -> pd.DataFrame:
    # Open-Meteo stamps radiation at the END of the hour it averages; the
    # physics expects interval starts, exactly as for the ERA5 archive.
    df = pd.DataFrame({"ts_utc": pd.to_datetime(hourly["time"]) - pd.Timedelta(hours=1)})
    for v, c in COLS.items():
        df[c] = pd.to_numeric(pd.Series(hourly.get(v + suffix)), errors="coerce").astype(float)
    df["pressure_kpa"] = df["pressure_kpa"] / 10.0
    # Short gaps (the last hours of a member, a missing step) are interpolated;
    # anything longer is left out rather than invented.
    df[list(COLS.values())] = df[list(COLS.values())].interpolate(limit=3, limit_direction="both")
    return df.dropna().reset_index(drop=True)


def fetch_cells(cells: dict[str, dict]) -> tuple[pd.DataFrame, dict]:
    """Deterministic hourly forecast (+ past days) for every weather cell."""
    ids = sorted(cells)
    frames, metas = [], []
    for start in range(0, len(ids), 80):
        batch = ids[start:start + 80]
        data, meta = _get(FORECAST_API, {
            "latitude": ",".join(f"{cells[c]['lat']:.3f}" for c in batch),
            "longitude": ",".join(f"{cells[c]['lon']:.3f}" for c in batch),
            "hourly": ",".join(VARS), "wind_speed_unit": "ms", "timezone": "GMT",
            "past_days": PAST_DAYS, "forecast_days": FORECAST_DAYS,
        }, f"cells_{start}")
        if len(data) != len(batch):
            raise WeatherUnavailable("location count mismatch")
        for cid, loc in zip(batch, data):
            f = _frame(loc["hourly"])
            f["cell"] = cid
            frames.append(f)
        metas.append(meta)
    meta = {"source": "Open-Meteo best-match NWP (deterministic, per ~5 km cell)",
            "fetched_at": min(m["fetched_at"] for m in metas),
            "stale": any(m.get("stale") for m in metas)}
    return pd.concat(frames, ignore_index=True), meta


def fetch_ensemble(points: dict[str, tuple[float, float]], model: str = "gfs_seamless") -> tuple[dict, dict]:
    """Ensemble members per city centre: {city: DataFrame with a ``member`` column}."""
    names = list(points)
    data, meta = _get(ENSEMBLE_API, {
        "latitude": ",".join(f"{points[n][0]:.3f}" for n in names),
        "longitude": ",".join(f"{points[n][1]:.3f}" for n in names),
        "hourly": ",".join(VARS), "models": model, "wind_speed_unit": "ms", "timezone": "GMT",
        "past_days": PAST_DAYS, "forecast_days": FORECAST_DAYS,
    }, f"ensemble_{model}")
    out = {}
    for n, loc in zip(names, data):
        h = loc["hourly"]
        members = sorted({k.rsplit("_member", 1)[1] for k in h if "_member" in k})
        frames = []
        for m in ["00"] + members:
            f = _frame(h, "" if m == "00" else f"_member{m}")
            if len(f) < 24:
                continue
            f["member"] = int(m)
            frames.append(f)
        out[n] = pd.concat(frames, ignore_index=True)
    meta = dict(meta, source=f"Open-Meteo ensemble ({model}, {len(members) + 1} members, city centre)",
                members=len(members) + 1)
    return out, meta


ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"


def fetch_recent(points: dict[str, tuple[float, float]], days: int = 16) -> tuple[dict, dict]:
    """The last ``days`` of reanalysis per city centre, so live HTSI has the
    history its duration channel and the mortality lags need (the forecast
    feeds carry only a few past days)."""
    names = list(points)
    end = (_now() - pd.Timedelta(days=1)).date()
    start = end - pd.Timedelta(days=days)
    data, meta = _get(ARCHIVE_API, {
        "latitude": ",".join(f"{points[n][0]:.3f}" for n in names),
        "longitude": ",".join(f"{points[n][1]:.3f}" for n in names),
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "hourly": ",".join(VARS), "wind_speed_unit": "ms", "timezone": "GMT",
    }, "recent_archive")
    return {n: _frame(loc["hourly"]) for n, loc in zip(names, data)}, dict(meta, source="Open-Meteo archive (ERA5/ERA5T)")


_ERA5: dict[str, pd.DataFrame] = {}


def era5(city: str) -> pd.DataFrame:
    if city not in _ERA5:
        df = pd.read_parquet(C.ERA5_DIR / f"{city}.parquet")
        df["ts_utc"] = pd.to_datetime(df["ts_utc"])
        _ERA5[city] = df
    return _ERA5[city]


def era5_window(city: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """ERA5 hours covering IST dates ``start``..``end`` inclusive."""
    df = era5(city)
    lo = start - pd.Timedelta(hours=C.IST_OFFSET_H + 1)
    hi = end + pd.Timedelta(days=1) - pd.Timedelta(hours=C.IST_OFFSET_H - 1)
    return df[(df["ts_utc"] >= lo) & (df["ts_utc"] < hi)].reset_index(drop=True)


def drill(df: pd.DataFrame, offset_c: float) -> pd.DataFrame:
    """Heatwave drill: shift air temperature and dewpoint together (RH held).

    Used only when an operator turns the labelled drill on; every payload built
    with it carries ``drill: +X degC``.
    """
    if not offset_c:
        return df
    out = df.copy()
    out["t2m"] = out["t2m"] + offset_c
    out["dewpoint"] = out["dewpoint"] + offset_c * 0.6
    return out


def nan_safe(df: pd.DataFrame) -> pd.DataFrame:
    return df.replace([np.inf, -np.inf], np.nan).dropna()
