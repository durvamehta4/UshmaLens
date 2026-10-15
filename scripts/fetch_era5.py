"""Download hourly ERA5 reanalysis for each pilot city from the Open-Meteo archive.

One point per city (ERA5 is ~25 km, so a city is one or two grid boxes). The
variables are exactly the ones the physics layer needs -- no estimated radiation.

    python scripts/fetch_era5.py            # 2014-01-01 .. 8 days ago, all cities
    python scripts/fetch_era5.py --city Mumbai --start 2024-01-01

Output: data/era5/<City>.parquet, hourly UTC.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ushmalens.cities import CITIES  # noqa: E402
from ushmalens.config import ERA5_DIR  # noqa: E402

API = "https://archive-api.open-meteo.com/v1/archive"
HOURLY = "temperature_2m,dew_point_2m,surface_pressure,wind_speed_10m,shortwave_radiation"


def fetch_city(name: str, start: date, end: date, client: httpx.Client) -> pd.DataFrame:
    lon, lat = CITIES[name]["centre"]
    frames = []
    y = start.year
    while y <= end.year:
        s = max(start, date(y, 1, 1))
        e = min(end, date(y + 2, 12, 31))
        for attempt in range(4):
            r = client.get(API, params={
                "latitude": lat, "longitude": lon, "start_date": s.isoformat(), "end_date": e.isoformat(),
                "hourly": HOURLY, "wind_speed_unit": "ms", "timezone": "GMT", "models": "era5",
            })
            if r.status_code == 429:
                time.sleep(20 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        h = r.json()["hourly"]
        frames.append(pd.DataFrame({
            # Radiation is the mean over the preceding hour, stamped at its end;
            # the physics expects interval starts.
            "ts_utc": pd.to_datetime(h["time"]) - pd.Timedelta(hours=1),
            "t2m": h["temperature_2m"], "dewpoint": h["dew_point_2m"],
            "pressure_kpa": [None if v is None else v / 10.0 for v in h["surface_pressure"]],
            "wind10": h["wind_speed_10m"], "ghi": h["shortwave_radiation"],
        }))
        print(f"  {name} {s}..{e}: {len(frames[-1]):,} h", flush=True)
        y += 3
    return pd.concat(frames, ignore_index=True).dropna().drop_duplicates("ts_utc")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", action="append")
    ap.add_argument("--start", default="2014-01-01")
    ap.add_argument("--end", default=(date.today() - timedelta(days=8)).isoformat())
    a = ap.parse_args(argv)
    with httpx.Client(timeout=180) as client:
        for name in a.city or list(CITIES):
            df = fetch_city(name, date.fromisoformat(a.start), date.fromisoformat(a.end), client)
            out = ERA5_DIR / f"{name}.parquet"
            df.to_parquet(out, index=False)
            print(f"{name}: {len(df):,} hourly rows -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
