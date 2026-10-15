"""The forecast engine: weather -> ward HTSI with uncertainty -> risk -> HAP levels.

Two clocks share one pipeline:

* **Live** -- today's NWP forecast per ~5 km weather cell, with the GFS ensemble
  supplying the probability that each day crosses each band edge. If the
  deterministic feed is unavailable (rate limit, outage) the ensemble control run
  stands in, labelled; if both fail the last good response is reused, labelled
  stale; if nothing is available the engine stays in replay.
* **Replay** -- any window of the 2014-2026 ERA5 record, issued day by day, so a
  past heatwave can be played forward and the alerts it would have produced
  audited. Replay forecasts are *perfect-prognosis* (the observed weather), with
  uncertainty bands of the width the forecaster carries at each lead time. They
  are labelled as such everywhere, and CAP documents from them say "Exercise".

A labelled **drill** offset (+X degC) can be applied on top of either.
"""

from __future__ import annotations

import json
import math
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ushmalens import config as C
from ushmalens import hap, risk
from ushmalens import science as S
from ushmalens import weather as W
from ushmalens.cities import CITIES
from ushmalens.places import Places

#: HTSI standard deviation by lead used when no ensemble is available (replay,
#: or ensemble outage). USHMA's held-out D+1 error on Odisha was MAE 3.5 HTSI
#: points (sd ~4.4); growth with lead follows its D+1..D+5 skill curve.
LEAD_SD = [0.0, 3.5, 4.5, 5.5, 6.0, 6.5]
LEADS = 6
Z80 = 1.2816


def today_ist() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc) + timedelta(hours=C.IST_OFFSET_H)).tz_localize(None).normalize()


def _r(v, d=1):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(f) else round(f, d)


def _norm_sf(x):
    return 0.5 * math.erfc(x / math.sqrt(2))


class Bundle:
    """Everything needed to answer questions about one clock: ward daily series
    over a span of dates, plus how uncertain each future date is."""

    def __init__(self, mode, dates, series, spread, sources, observed_through, drill):
        self.mode = mode
        self.dates = dates                    # issuance dates the clock can sit on
        self.units, self.loc = series         # unit id -> arrays; ward key -> (unit, row)
        self.spread = spread                  # city -> {date: np.ndarray offsets} (live) or None
        self.sources = sources
        self.observed_through = observed_through
        self.drill = drill
        self.light = {}                       # (key, issued) -> minimal day dicts for the HAP
        self.built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")


class Engine:
    def __init__(self):
        self.places = Places()
        self.clim = json.loads((C.BUILD_DIR / "climatology.json").read_text())
        self.backtest = json.loads((C.BUILD_DIR / "backtest.json").read_text())
        self.curves = risk.load_curves(self.backtest)
        self.cells = self.places.assign_cells()
        self.lock = threading.RLock()
        self.listeners = []
        self.mode = "replay"
        self.drill = 0.0
        self.live: Bundle | None = None
        self.live_error: str | None = None
        self.live_raw = None
        self.replay: Bundle | None = None
        self.window = self.default_window()
        self.as_of = None
        self.states: dict = {}
        self.transitions: list = []
        self._payload_cache: dict = {}
        self.set_window(self.window["start"], self.window["end"], self.window.get("asOf"))

    # ------------------------------------------------------------------ events
    def on(self, fn):
        self.listeners.append(fn)

    def emit(self, kind, data):
        for fn in list(self.listeners):
            try:
                fn(kind, data)
            except Exception:  # noqa: BLE001 - a broken listener must not break the engine
                pass

    # ------------------------------------------------------------------ replay windows
    def episodes(self) -> list[dict]:
        out = []
        for city, b in self.backtest["cities"].items():
            for e in b["episodes"]:
                out.append({"id": f"{city}-{e['peak']}", "city": city, **e,
                            "label": f"{city} · {pd.Timestamp(e['peak']).strftime('%b %Y')} (peak HTSI {e['maxHtsi']})"})
        return sorted(out, key=lambda e: -e["maxHtsi"])

    def default_window(self) -> dict:
        eps = [e for e in self.episodes() if e["city"] == "Ahmedabad"]
        e = max(eps, key=lambda x: x["maxHtsi"]) if eps else self.episodes()[0]
        return {"start": e["start"], "end": e["end"], "asOf": (pd.Timestamp(e["peak"]) - pd.Timedelta(days=4)).date().isoformat(),
                "episode": e["id"]}

    def era5_range(self):
        r = self.backtest["cities"][next(iter(CITIES))]["range"]
        return r

    # ------------------------------------------------------------------ building
    def _ward_frames(self, daily_by_unit: dict, unit_of) -> tuple[dict, dict]:
        """HTSI + risk arrays for every ward, computed once per weather unit."""
        groups = {}
        for w in self.places.wards.values():
            groups.setdefault(unit_of(w), []).append(w)
        units, loc = {}, {}
        for uid, wards in groups.items():
            city = wards[0].city
            m = S.htsi_matrix(daily_by_unit[uid], self.clim[city],
                              [w.d_day for w in wards], [w.d_night for w in wards])
            rk = risk.ward_risk(m["htsi"], [w.population for w in wards], [w.vuln_mult for w in wards],
                                city, self.curves[city])
            m.update(rk)
            m["band"] = S.band_of(m["htsi"])
            m["pos"] = {d.date().isoformat(): i for i, d in enumerate(m["dates"])}
            m["keys"] = [w.key for w in wards]
            units[uid] = m
            for i, w in enumerate(wards):
                loc[w.key] = (uid, i)
        return units, loc

    def build_replay(self, start: str, end: str, drill: float = 0.0) -> Bundle:
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        lo, hi = s - pd.Timedelta(days=12), e + pd.Timedelta(days=LEADS)
        daily = {}
        for city, cfg in CITIES.items():
            raw = W.drill(W.era5_window(city, lo, hi), drill)
            if raw.empty:
                raise ValueError(f"no ERA5 data for {city} in {start}..{end}")
            hr = S.hourly_indices(raw, cfg["centre"][1], cfg["centre"][0])
            hr["unit"] = city
            dd = S.daily_aggregate(hr)
            daily[city] = dd[dd["complete"]].reset_index(drop=True)
        series = self._ward_frames(daily, lambda w: w.city)
        last = min(d["date"].max() for d in daily.values())
        dates = [d.date().isoformat() for d in pd.date_range(s, min(e, last))]
        return Bundle("replay", dates, series, None,
                      {"weather": "ERA5 reanalysis (Open-Meteo archive), city centre; perfect-prognosis replay",
                       "uncertainty": "lead-time error widths (no ensemble in replay)"},
                      None, drill)

    def fetch_live_raw(self) -> dict:
        """Network part of a live refresh; kept separate so a drill can reuse it."""
        raw, errors = {"cells": None, "ens": None, "meta": {}}, []
        try:
            raw["cells"], raw["meta"]["cells"] = W.fetch_cells(self.cells)
        except W.WeatherUnavailable as e:
            errors.append(f"deterministic: {e}")
        pts = {c: (cfg["centre"][1], cfg["centre"][0]) for c, cfg in CITIES.items()}
        try:
            raw["ens"], raw["meta"]["ensemble"] = W.fetch_ensemble(pts)
        except W.WeatherUnavailable as e:
            errors.append(f"ensemble: {e}")
        try:
            raw["recent"], raw["meta"]["recent"] = W.fetch_recent(pts)
        except W.WeatherUnavailable as e:
            raw["recent"] = None
            errors.append(f"recent history: {e}")
        raw["errors"] = errors
        if raw["cells"] is None and raw["ens"] is None:
            raise W.WeatherUnavailable("; ".join(errors))
        return raw

    def build_live(self, raw: dict, drill: float = 0.0) -> Bundle:
        today = today_ist()
        sources = {"errors": raw["errors"]}
        daily = {}
        if raw["cells"] is not None:
            for cid, g in raw["cells"].groupby("cell"):
                c = self.cells[cid]
                hr = S.hourly_indices(W.drill(g.drop(columns="cell"), drill), c["lat"], c["lon"])
                hr["unit"] = cid
                dd = S.daily_aggregate(hr)
                daily[cid] = dd[dd["complete"]].reset_index(drop=True)
            unit_of = (lambda w: w.cell)
            sources["weather"] = raw["meta"]["cells"]["source"]
            sources["weatherFetchedAt"] = raw["meta"]["cells"]["fetched_at"]
            sources["weatherStale"] = raw["meta"]["cells"].get("stale", False)
        spread, ens_daily = {}, {}
        if raw["ens"] is not None:
            for city, f in raw["ens"].items():
                cfg = CITIES[city]
                hr = S.hourly_indices(W.drill(f, drill), cfg["centre"][1], cfg["centre"][0])
                hr["unit"] = hr["member"]
                dd = S.daily_aggregate(hr)
                dd = dd[dd["complete"]]
                mats = {}
                for m, g in dd.groupby("unit"):
                    hs = S.htsi_series(g, self.clim[city])
                    mats[m] = hs.set_index("date")["htsi"]
                M = pd.DataFrame(mats)          # dates x members
                M = M[M.index >= today]
                spread[city] = {d.date().isoformat(): (row.dropna().to_numpy() - np.nanmedian(row.to_numpy()))
                                for d, row in M.iterrows() if row.notna().sum() >= 10}
                ens_daily[city] = dd[dd["unit"] == 0].drop(columns="unit").assign(unit=city).reset_index(drop=True)
            sources["ensemble"] = raw["meta"]["ensemble"]["source"]
            sources["ensembleFetchedAt"] = raw["meta"]["ensemble"]["fetched_at"]
        if not daily:
            # Deterministic feed down: the ensemble control run stands in, per city.
            daily = ens_daily
            unit_of = (lambda w: w.city)
            sources["weather"] = "Open-Meteo GFS ensemble control run (deterministic feed unavailable), city centre"
            sources["weatherFetchedAt"] = raw["meta"]["ensemble"]["fetched_at"]
            sources["weatherStale"] = raw["meta"]["ensemble"].get("stale", False)
        # Stitch recent reanalysis days in front of each unit's forecast.
        recent = {}
        for city, f in (raw.get("recent") or {}).items():
            cfg = CITIES[city]
            hr = S.hourly_indices(W.drill(f, drill), cfg["centre"][1], cfg["centre"][0])
            hr["unit"] = city
            dd = S.daily_aggregate(hr)
            recent[city] = dd[dd["complete"]].drop(columns="unit")
        if recent:
            city_of_unit = {cid: c["city"] for cid, c in self.cells.items()}
            for uid in list(daily):
                city = city_of_unit.get(uid, uid)
                if city in recent:
                    d = daily[uid]
                    old = recent[city][recent[city]["date"] < d["date"].min()].assign(unit=uid)
                    daily[uid] = pd.concat([old, d], ignore_index=True)
            sources["history"] = raw["meta"]["recent"]["source"]
        sources["uncertainty"] = ("ensemble spread transferred to wards" if spread
                                  else "lead-time error widths (ensemble unavailable)")
        series = self._ward_frames(daily, unit_of)
        first = max(u["dates"].min() for u in series[0].values())
        start = min(max(first + pd.Timedelta(days=4), today - pd.Timedelta(days=5)), today)
        dates = [d.date().isoformat() for d in pd.date_range(start, today)]
        return Bundle("live", dates, series, spread or None, sources,
                      (today - pd.Timedelta(days=1)).date().isoformat(), drill)

    # ------------------------------------------------------------------ clock
    def set_window(self, start: str, end: str, as_of: str | None = None, episode: str | None = None):
        b = self.build_replay(start, end, self.drill)
        with self.lock:
            self.replay = b
            self.window = {"start": start, "end": end, "episode": episode}
            self.mode = "replay"
            self.as_of = as_of if as_of in b.dates else b.dates[0]
            self._recompute()
        self.emit("clock", self.clock())

    def set_as_of(self, as_of: str):
        with self.lock:
            b = self.bundle
            if as_of not in b.dates:
                raise ValueError(f"{as_of} is outside the {b.mode} window {b.dates[0]}..{b.dates[-1]}")
            self.as_of = as_of
            self._recompute()
        self.emit("clock", self.clock())

    def refresh_live(self, switch: bool = True) -> dict:
        try:
            raw = self.fetch_live_raw()
        except W.WeatherUnavailable as e:
            self.live_error = str(e)
            raise
        b = self.build_live(raw, self.drill)
        with self.lock:
            self.live_raw, self.live, self.live_error = raw, b, None
            if switch:
                self.mode = "live"
                self.as_of = b.dates[-1]
                self._recompute()
        if switch:
            self.emit("clock", self.clock())
        return self.clock()

    def use_live(self):
        if not self.live:
            raise ValueError("no live forecast yet")
        with self.lock:
            self.mode = "live"
            self.as_of = self.live.dates[-1]
            self._recompute()
        self.emit("clock", self.clock())

    def set_drill(self, offset: float):
        offset = float(max(-3.0, min(6.0, offset)))
        with self.lock:
            self.drill = offset
            self.replay = self.build_replay(self.window["start"], self.window["end"], offset)
            if self.live_raw:
                self.live = self.build_live(self.live_raw, offset)
            if self.mode == "live" and self.live:
                self.as_of = self.live.dates[-1]
            elif self.as_of not in self.replay.dates:
                self.as_of = self.replay.dates[0]
            self._recompute()
        self.emit("clock", self.clock())

    @property
    def bundle(self) -> Bundle:
        return self.live if self.mode == "live" and self.live else self.replay

    def clock(self) -> dict:
        b = self.bundle
        return {"mode": self.mode, "asOf": self.as_of, "dates": b.dates, "window": self.window,
                "drill": self.drill, "liveAvailable": self.live is not None, "liveError": self.live_error,
                "liveBuiltAt": self.live.built_at if self.live else None, "sources": b.sources,
                "era5Range": self.era5_range()}

    # ------------------------------------------------------------------ days
    def days_for(self, key: str, issued: str, b: Bundle | None = None) -> list[dict]:
        b = b or self.bundle
        uid, j = b.loc[key]
        u = b.units[uid]
        base = u["base"]
        city = key.split(":", 1)[0]
        i0 = u["pos"].get(issued)
        if i0 is None:
            return []
        out = []
        for k in range(LEADS):
            i = i0 + k
            if i >= len(u["dates"]):
                break
            ds = u["dates"][i].date().isoformat()
            h = float(u["htsi"][j, i])
            observed = (b.mode == "replay" and k == 0) or (b.mode == "live" and b.observed_through and ds <= b.observed_through)
            spread = None if observed else (b.spread or {}).get(city, {}).get(ds)
            if observed:
                q10 = q90 = h
                p = [float(h >= e) for e in C.BAND_EDGES]
            elif spread is not None and len(spread):
                vals = h + spread
                q10, q90 = np.quantile(vals, [0.1, 0.9])
                p = [float((vals >= e).mean()) for e in C.BAND_EDGES]
            else:
                if b.mode == "replay":
                    lead = k
                else:
                    lead = (pd.Timestamp(ds) - pd.Timestamp(b.observed_through)).days
                sd = LEAD_SD[max(0, min(lead, LEADS - 1))]
                q10, q90 = h - Z80 * sd, h + Z80 * sd
                p = [float(h >= e) if sd == 0 else _norm_sf((e - h) / sd) for e in C.BAND_EDGES]
            out.append({
                "lead": k, "date": ds, "htsi": _r(h), "q10": _r(max(q10, 0)), "q90": _r(min(q90, 100)),
                "band": u["band"][j, i], "pWatch": _r(p[0], 2), "pWarning": _r(p[1], 2), "pEmergency": _r(p[2], 2),
                "observed": bool(observed),
                "tmax": _r(base["t2m_max"].iat[i]), "tmin": _r(base["t2m_min"].iat[i]), "rhMean": _r(base["rh_mean"].iat[i], 0),
                "wbgt": _r(u["wbgt_max_w"][j, i]), "utci": _r(u["utci_max_w"][j, i]),
                "nightWbgt": _r(u["wbgt_night_min_w"][j, i]), "nightTmin": _r(u["t2m_night_min_w"][j, i]),
                "heatIndex": _r(base["heat_index_max"].iat[i]), "wbgtHours30": int(base["wbgt_hours_30"].iat[i]),
                "channels": {"intensity": _r(u["c_intensity"][j, i]), "anomaly": _r(u["c_anomaly"][j, i]),
                             "nightRelief": _r(u["c_night_relief"][j, i]), "duration": _r(u["c_duration"][j, i])},
                "pctRank": _r(u["pct_rank"][i], 0), "regime": str(u["anomaly_regime"][i]), "spellDays": int(u["spell_days"][i]),
                "rr": _r(u["rr_central"][j, i], 4), "mri": _r(u["mri"][j, i]),
                "expectedDeaths": _r(u["expected"][j, i], 3), "excessDeaths": _r(u["excess_central"][j, i], 3),
                "excessLow": _r(u["excess_low"][j, i], 3), "excessHigh": _r(u["excess_high"][j, i], 3),
                "edSurge": _r(u["ed"][j, i], 1), "edLow": _r(u["ed_low"][j, i], 1), "edHigh": _r(u["ed_high"][j, i], 1),
            })
        return out

    def history_for(self, key: str, before: str, n: int = 21, b: Bundle | None = None) -> list[dict]:
        b = b or self.bundle
        uid, j = b.loc[key]
        u = b.units[uid]
        idx = [i for i, d in enumerate(u["dates"]) if d < pd.Timestamp(before)][-n:]
        return [{"date": u["dates"][i].date().isoformat(), "htsi": _r(u["htsi"][j, i]), "band": u["band"][j, i],
                 "wbgt": _r(u["wbgt_max_w"][j, i]), "tmax": _r(u["base"]["t2m_max"].iat[i]),
                 "excess": _r(u["excess_central"][j, i], 3)} for i in idx]

    def city_days(self, city: str, issued: str, b: Bundle | None = None) -> list[dict]:
        b = b or self.bundle
        per = [self.days_for(w.key, issued, b) for w in self.places.by_city[city]]
        n = min(len(p) for p in per)
        out = []
        for k in range(n):
            col = [p[k] for p in per]
            arr = lambda f: np.array([c[f] if c[f] is not None else np.nan for c in col], dtype=float)  # noqa: E731
            h = arr("htsi")
            hv = float(np.nanquantile(h, 0.9))
            out.append({
                "lead": k, "date": col[0]["date"], "htsi": _r(hv), "htsiMax": _r(np.nanmax(h)),
                "q10": _r(np.nanquantile(arr("q10"), 0.9)), "q90": _r(np.nanquantile(arr("q90"), 0.9)),
                "band": S.band_of([hv])[0], "observed": col[0]["observed"],
                "pWatch": _r(np.nanquantile(arr("pWatch"), 0.9), 2), "pWarning": _r(np.nanquantile(arr("pWarning"), 0.9), 2),
                "pEmergency": _r(np.nanquantile(arr("pEmergency"), 0.9), 2),
                "wbgt": _r(np.nanmax(arr("wbgt"))), "nightWbgt": _r(np.nanmax(arr("nightWbgt"))),
                "tmax": _r(np.nanmax(arr("tmax"))), "utci": _r(np.nanmax(arr("utci"))),
                "excessDeaths": _r(np.nansum(arr("excessDeaths")), 2), "excessLow": _r(np.nansum(arr("excessLow")), 2),
                "excessHigh": _r(np.nansum(arr("excessHigh")), 2), "edSurge": _r(np.nansum(arr("edSurge")), 0),
                "mri": _r(np.nanquantile(arr("mri"), 0.9)),
                "wardsByBand": {bd: int(sum(c["band"] == bd for c in col)) for bd in C.BANDS},
            })
        return out

    # ------------------------------------------------------------------ HAP
    def _places_iter(self):
        for w in self.places.wards.values():
            yield "ward", w.key, w.name, w.city
        for city in CITIES:
            yield "city", city, city, city

    def _light_days(self, key: str, issued: str, b: Bundle) -> list[dict]:
        """Only what the HAP state machine reads; cached per bundle."""
        ck = (key, issued)
        if ck in b.light:
            return b.light[ck]
        if key in CITIES:
            per = [self._light_days(w.key, issued, b) for w in self.places.by_city[key]]
            n = min(len(p) for p in per)
            out = []
            for k in range(n):
                col = np.array([[p[k]["htsi"], p[k]["q10"], p[k]["q90"], p[k]["pWatch"], p[k]["pWarning"],
                                 p[k]["pEmergency"]] for p in per], dtype=float)
                q = np.nanquantile(col, 0.9, axis=0)
                out.append({"lead": k, "date": per[0][k]["date"], "htsi": _r(q[0]), "q10": _r(q[1]), "q90": _r(q[2]),
                            "band": S.band_of([q[0]])[0], "pWatch": _r(q[3], 2), "pWarning": _r(q[4], 2),
                            "pEmergency": _r(q[5], 2)})
        else:
            out = self._light_ward(key, issued, b)
        b.light[ck] = out
        return out

    def _light_ward(self, key, issued, b):
        uid, j = b.loc[key]
        u = b.units[uid]
        city = key.split(":", 1)[0]
        i0 = u["pos"].get(issued)
        if i0 is None:
            return []
        out = []
        for k in range(min(LEADS, len(u["dates"]) - i0)):
            i = i0 + k
            ds = u["dates"][i].date().isoformat()
            h = float(u["htsi"][j, i])
            observed = (b.mode == "replay" and k == 0) or (b.mode == "live" and b.observed_through and ds <= b.observed_through)
            spread = None if observed else (b.spread or {}).get(city, {}).get(ds)
            if observed:
                q10 = q90 = h
                p = [float(h >= e) for e in C.BAND_EDGES]
            elif spread is not None and len(spread):
                vals = h + spread
                q10, q90 = np.quantile(vals, [0.1, 0.9])
                p = [float((vals >= e).mean()) for e in C.BAND_EDGES]
            else:
                lead = k if b.mode == "replay" else (pd.Timestamp(ds) - pd.Timestamp(b.observed_through)).days
                sd = LEAD_SD[max(0, min(lead, LEADS - 1))]
                q10, q90 = h - Z80 * sd, h + Z80 * sd
                p = [float(h >= e) if sd == 0 else _norm_sf((e - h) / sd) for e in C.BAND_EDGES]
            out.append({"lead": k, "date": ds, "htsi": round(h, 1), "q10": round(max(q10, 0), 1),
                        "q90": round(min(q90, 100), 1), "band": u["band"][j, i],
                        "pWatch": round(p[0], 2), "pWarning": round(p[1], 2), "pEmergency": round(p[2], 2)})
        return out

    def _recompute(self):
        b = self.bundle
        dates = [d for d in b.dates if d <= self.as_of]
        prev = self.states
        states, transitions = {}, []
        for scope, pid, name, city in self._places_iter():
            seq = [{"date": d, "days": days} for d in dates if (days := self._light_days(pid, d, b))]
            if not seq:
                continue
            res = hap.run_sequence(seq)
            st = res["state"]
            key = f"{scope}:{pid}"
            full = self.days_for(pid, seq[-1]["date"], b) if scope == "ward" else self.city_days(pid, seq[-1]["date"], b)
            states[key] = {
                "key": key, "scope": scope, "id": pid, "name": name, "city": city,
                "level": st["level"], "band": hap.LEVELS[st["level"]], "since": st["since"],
                "target": res["target"], "today": full[0], "forecast": full[1:],
                "alertId": f"UL-{scope}-{pid}-{st['since']}".replace(":", "-").replace("/", "_") if st["level"] > 0 else None,
                "transitions": res["transitions"],
            }
            for t in res["transitions"]:
                transitions.append({**t, "key": key, "scope": scope, "id": pid, "name": name, "city": city})
        fresh = [{"key": k, "name": s["name"], "scope": s["scope"], "from": prev[k]["band"], "to": s["band"], "asOf": self.as_of}
                 for k, s in states.items() if k in prev and prev[k]["level"] != s["level"]]
        self.states = states
        self.transitions = sorted(transitions, key=lambda t: t["date"], reverse=True)
        self._payload_cache = {}
        if fresh:
            self.emit("transitions", fresh[:200])

    # ------------------------------------------------------------------ queries
    def active(self, scope=None, city=None, min_level=1) -> list[dict]:
        xs = [s for s in self.states.values() if s["level"] >= min_level
              and (not scope or s["scope"] == scope) and (not city or s["city"] == city)]
        return sorted(xs, key=lambda s: (-s["level"], -(s["today"].get("mri") or 0)))

    def alert_record(self, alert_id: str):
        for s in self.states.values():
            if s["alertId"] == alert_id:
                lead = (s["target"] or {}).get("trigger", {}).get("lead", 0) or 0
                day = s["today"] if lead == 0 else s["forecast"][lead - 1]
                geom = self.places.wards[s["id"]].geometry if s["scope"] == "ward" else None
                return {"state": s, "lead": lead, "day": day, "geometry": geom}
        return None

    def snapshot(self, city: str, lead: int) -> dict:
        ck = ("snap", city, lead, self.mode, self.as_of, self.drill)
        if ck in self._payload_cache:
            return self._payload_cache[ck]
        wards = {}
        for w in self.places.city_wards(city):
            days = self.days_for(w.key, self.as_of)
            if lead >= len(days):
                continue
            d = days[lead]
            st = self.states.get(f"ward:{w.key}")
            wards[w.key] = {k: d[k] for k in ("htsi", "q10", "q90", "band", "pWarning", "pWatch", "mri", "excessDeaths",
                                               "excessLow", "excessHigh", "wbgt", "utci", "nightWbgt", "tmax")}
            wards[w.key]["excessPer100k"] = _r(1e5 * (d["excessDeaths"] or 0) / max(w.population, 1), 3)
            wards[w.key]["hap"] = st["band"] if st else "Normal"
            wards[w.key]["vuln"] = round(w.vuln_score, 3)
            wards[w.key]["vulnMult"] = round(w.vuln_mult, 3)
            wards[w.key]["uhiNight"] = round(w.d_night, 2)
        cd = self.city_days(city, self.as_of)
        out = {"city": city, "mode": self.mode, "asOf": self.as_of, "lead": lead,
               "date": cd[lead]["date"] if lead < len(cd) else None,
               "dates": [d["date"] for d in cd], "wards": wards, "cityDays": cd,
               "cityHap": self.states.get(f"city:{city}")}
        self._payload_cache[ck] = out
        return out

    def overview(self) -> dict:
        cities = []
        for city, cfg in CITIES.items():
            cd = self.city_days(city, self.as_of)
            st = self.states.get(f"city:{city}")
            wards_lv = [s for s in self.states.values() if s["scope"] == "ward" and s["city"] == city]
            cities.append({
                "city": city, "state": cfg["state"], "lon": cfg["centre"][0], "lat": cfg["centre"][1],
                "wards": len(self.places.by_city[city]), "hap": st["band"] if st else "Normal",
                "hapReason": (st or {}).get("target", {}).get("reason") if st else None,
                "wardsByHap": {bd: sum(1 for s in wards_lv if s["band"] == bd) for bd in C.BANDS},
                "days": cd,
                "excess3d": _r(sum((d["excessDeaths"] or 0) for d in cd[1:4]), 2),
                "population": round(sum(w.population for w in self.places.by_city[city])),
            })
        return {"mode": self.mode, "asOf": self.as_of, "drill": self.drill, "cities": cities}


_ENGINE: Engine | None = None
_ENGINE_LOCK = threading.Lock()


def get_engine() -> Engine:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            t = time.time()
            _ENGINE = Engine()
            print(f"[ushmalens] engine ready in {time.time() - t:.1f}s "
                  f"({len(_ENGINE.places.wards)} wards, replay {_ENGINE.window['start']}..{_ENGINE.window['end']})", flush=True)
        return _ENGINE
