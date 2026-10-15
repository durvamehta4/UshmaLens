"""UshmaLens HTTP API and dashboard host.

    python -m ushmalens            # http://127.0.0.1:8000

Read routes are open. Routes that move the clock, send messages or change
subscriptions need the ``x-api-key`` header (USHMALENS_API_KEY).
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import threading
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ushmalens import config as C
from ushmalens import alerts as A
from ushmalens import weather as W
from ushmalens.cities import CITIES
from ushmalens.engine import LEADS, get_engine

app = FastAPI(title="UshmaLens", version="1.0",
              description="Ward-level heat-health early warning: hourly physiology -> HTSI -> excess deaths -> Heat Action Plan.")
ENGINE = None
STORE: A.Store | None = None
SUBSCRIBERS: set = set()
LOOP = None


# --------------------------------------------------------------------------- plumbing
def eng():
    return ENGINE


def require_key(x_api_key: str | None = Header(default=None)):
    if x_api_key != C.API_KEY:
        raise HTTPException(401, "missing or wrong x-api-key")


def _broadcast(kind, data):
    if LOOP is None:
        return
    msg = f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n"
    for q in list(SUBSCRIBERS):
        LOOP.call_soon_threadsafe(q.put_nowait, msg)


def _scheduler():
    """Fetch the live forecast in the background; keep it fresh."""
    while True:
        try:
            ENGINE.refresh_live(switch=ENGINE.mode == "live")
            STORE.audit({"type": "live-refresh", "status": "ok", "sources": ENGINE.live.sources})
            _broadcast("live", {"available": True, "builtAt": ENGINE.live.built_at})
        except Exception as e:  # noqa: BLE001
            STORE.audit({"type": "live-refresh", "status": "failed", "error": str(e)[:300]})
            _broadcast("live", {"available": ENGINE.live is not None, "error": str(e)[:300]})
        time.sleep(C.LIVE_REFRESH_S)


@app.on_event("startup")
async def _startup():
    global ENGINE, STORE, LOOP
    LOOP = asyncio.get_running_loop()
    ENGINE = await asyncio.to_thread(get_engine)
    STORE = A.Store()
    ENGINE.on(_broadcast)
    if C.API_KEY == "ushmalens-dev-key":
        print("[ushmalens] WARNING: using the default API key; set USHMALENS_API_KEY in production", flush=True)
    if not C.NO_SCHEDULER:
        threading.Thread(target=_scheduler, daemon=True).start()


def _city(city: str) -> str:
    if city not in CITIES:
        raise HTTPException(404, f"unknown city {city}")
    return city


def _ward(city, wid):
    try:
        return ENGINE.places.get(_city(city), wid)
    except KeyError:
        raise HTTPException(404, f"unknown ward {city}/{wid}") from None


# --------------------------------------------------------------------------- system
@app.get("/api/health")
def health():
    e = eng()
    return {"status": "ok", "mode": e.mode, "asOf": e.as_of, "wards": len(e.places.wards),
            "liveAvailable": e.live is not None}


@app.get("/api/meta")
def meta():
    e = eng()
    return {
        "name": "UshmaLens", "bands": C.BANDS, "bandEdges": C.BAND_EDGES,
        "cities": [{"city": c, "state": v["state"], "lang": v["lang"], "lon": v["centre"][0], "lat": v["centre"][1],
                    "wards": len(e.places.by_city[c]), "coastal": v["coastal"],
                    "districtHvi": {k: e.places.hvi.get(c, {}).get(k) for k in
                                    ("outdoor_workers_pct", "mpi_poverty_pct", "healthcare_travel_min", "hvi_score", "hvi_tier")},
                    **e.places.city_meta[c]} for c, v in CITIES.items()],
        "languages": [{"code": k, "name": v["_meta"]["language"], "review": v["_meta"]["review"]} for k, v in A.LANGS.items()],
        "audiences": [{"id": a, "label": A.LANGS["en"]["audiences"][a]["label"]} for a in A.AUDIENCES],
        "channels": A.CHANNELS, "channelMode": C.CHANNEL_MODE,
        "htsiWeights": C.HTSI_WEIGHTS, "hap": C.HAP, "interventions": C.INTERVENTIONS,
        "riskCurves": {c: cv.public() for c, cv in e.curves.items()},
        "defaultKey": C.API_KEY == "ushmalens-dev-key",
    }


# --------------------------------------------------------------------------- clock
class AsOf(BaseModel):
    date: str


class Window(BaseModel):
    start: str
    end: str
    asOf: str | None = None
    episode: str | None = None


class Drill(BaseModel):
    offset: float = Field(0.0, ge=-3, le=6)


@app.get("/api/clock")
def clock():
    return eng().clock()


@app.get("/api/episodes")
def episodes():
    return eng().episodes()


@app.post("/api/clock/asof", dependencies=[Depends(require_key)])
def set_asof(b: AsOf):
    try:
        eng().set_as_of(b.date)
    except ValueError as ex:
        raise HTTPException(400, str(ex)) from None
    return eng().clock()


@app.post("/api/clock/window", dependencies=[Depends(require_key)])
def set_window(b: Window):
    try:
        eng().set_window(b.start, b.end, b.asOf, b.episode)
    except ValueError as ex:
        raise HTTPException(400, str(ex)) from None
    STORE.audit({"type": "clock-window", "start": b.start, "end": b.end, "episode": b.episode})
    return eng().clock()


@app.post("/api/live/refresh", dependencies=[Depends(require_key)])
def live_refresh():
    try:
        c = eng().refresh_live(switch=True)
    except W.WeatherUnavailable as ex:
        STORE.audit({"type": "live-refresh", "status": "failed", "error": str(ex)[:300]})
        raise HTTPException(502, f"live weather unavailable: {ex}") from None
    STORE.audit({"type": "live-refresh", "status": "ok", "sources": c["sources"]})
    return c


@app.post("/api/live/use", dependencies=[Depends(require_key)])
def live_use():
    try:
        eng().use_live()
    except ValueError as ex:
        raise HTTPException(409, str(ex)) from None
    return eng().clock()


@app.post("/api/drill", dependencies=[Depends(require_key)])
def drill(b: Drill):
    eng().set_drill(b.offset)
    STORE.audit({"type": "drill", "offset": b.offset})
    return eng().clock()


# --------------------------------------------------------------------------- geography & map
@app.get("/api/overview")
def overview():
    return eng().overview()


@app.get("/api/india")
def india():
    return FileResponse(C.DATA / "national" / "india_simp.geojson", media_type="application/geo+json")


@app.get("/api/cities/{city}/geometry")
def geometry(city: str, response: Response):
    response.headers["cache-control"] = "public, max-age=86400"
    return eng().places.geojson(_city(city))


@app.get("/api/cities/{city}/basemap.jpg")
def basemap(city: str):
    return FileResponse(C.CITY_DIR / _city(city) / "basemap.jpg", media_type="image/jpeg")


@app.get("/api/cities/{city}/snapshot")
def snapshot(city: str, lead: int = 0):
    if not 0 <= lead < LEADS:
        raise HTTPException(400, "lead must be 0-5")
    return eng().snapshot(_city(city), lead)


@app.get("/api/cities/{city}/wards")
def city_wards(city: str):
    import re
    nat = lambda w: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", w.name)]  # noqa: E731
    return [w.public() for w in sorted(eng().places.city_wards(_city(city)), key=nat)]


@app.get("/api/wards/{city}/{wid:path}")
def ward_detail(city: str, wid: str):
    e = eng()
    w = _ward(city, wid)
    days = e.days_for(w.key, e.as_of)
    st = e.states.get(f"ward:{w.key}")
    d1 = days[1] if len(days) > 1 else days[0]
    curve = e.curves[city]
    lead = (st or {}).get("target", {}).get("trigger", {}).get("lead", 0) if st else 0
    explain = {
        "uhi": f"Urban heat island: +{w.d_day:.1f} °C by day, +{w.d_night:.1f} °C at night "
               f"(built-up {w.built:.0%}{'' if w.lst_anom is None else f', MODIS LST {w.lst_anom:+.1f} °C vs city median'}).",
        "anomaly": f"Tomorrow sits at the {d1['pctRank']:.0f}th percentile of this time of year "
                   f"({'dry heat: air temperature' if d1['regime'] == 'dry' else 'humid heat: WBGT'} is the more unusual).",
        "risk": f"Baseline {d1['expectedDeaths']:.3f} deaths/day x (RR {d1['rr']:.3f} - 1) = {d1['excessDeaths']:.3f} excess "
                f"(vulnerability x{w.vuln_mult:.2f} on the log scale; curve anchored to RR {curve.rr[1]} at HTSI {curve.reference_htsi}).",
    }
    adv = A.render_advisory(w.name, st["band"] if st else "Normal", lead, days[min(lead, len(days) - 1)],
                            CITIES[city]["lang"])
    return {**w.public(), "geometry": w.geometry, "asOf": e.as_of, "mode": e.mode, "days": days,
            "history": e.history_for(w.key, e.as_of), "hap": st, "explain": explain, "advisory": adv,
            "curve": curve.public()}


# --------------------------------------------------------------------------- alerts
@app.get("/api/alerts/active")
def alerts_active(scope: str | None = None, city: str | None = None, minLevel: int = 1):
    e = eng()
    xs = e.active(scope, city, minLevel)
    return {"mode": e.mode, "asOf": e.as_of, "drill": e.drill, "count": len(xs),
            "counts": {b: sum(1 for s in xs if s["band"] == b) for b in C.BANDS[1:]},
            "alerts": [{k: v for k, v in s.items() if k != "transitions"} | {"lastTransition": (s["transitions"] or [None])[-1]}
                       for s in xs]}


@app.get("/api/alerts/history")
def alerts_history(limit: int = 300, city: str | None = None):
    t = eng().transitions
    if city:
        t = [x for x in t if x["city"] == city]
    return {"asOf": eng().as_of, "transitions": t[:limit]}


@app.get("/api/alerts/{alert_id}/cap.xml")
def cap(alert_id: str):
    e = eng()
    rec = e.alert_record(alert_id)
    if not rec:
        raise HTTPException(404, "no active alert with that id")
    s = rec["state"]
    geom = e.places.wards[s["id"]].geometry if s["scope"] == "ward" else None
    langs = ["en", "hi", CITIES[s["city"]]["lang"]]
    xml = A.build_cap(alert_id, s["band"], rec["lead"], rec["day"], s["name"], geom, f"{s['scope']}:{s['id']}",
                      actual=e.mode == "live" and not e.drill, langs=langs)
    return Response(xml, media_type="application/cap+xml")


class Dispatch(BaseModel):
    dryRun: bool = False


@app.post("/api/alerts/dispatch", dependencies=[Depends(require_key)])
def alerts_dispatch(b: Dispatch):
    res = A.dispatch(eng(), STORE, b.dryRun)
    STORE.audit({"type": "dispatch", "asOf": eng().as_of, "mode": eng().mode, "messages": len(res), "dryRun": b.dryRun})
    if res and not b.dryRun:
        _broadcast("deliveries", res[:50])
    return {"asOf": eng().as_of, "dispatched": res}


@app.get("/api/alerts/{alert_id}/checklist")
def checklist(alert_id: str):
    rec = eng().alert_record(alert_id)
    if not rec:
        raise HTTPException(404, "no active alert with that id")
    lvl = rec["state"]["band"]
    items = []
    for aud in ("municipal", "hospitals", "power_utility", "schools"):
        for t in A.LANGS["en"]["audiences"][aud].get(lvl, []):
            items.append({"audience": A.LANGS["en"]["audiences"][aud]["label"], "item": t})
    done = STORE.checked(alert_id)
    return {"alertId": alert_id, "level": lvl, "items": [dict(i, done=i["item"] in done, **done.get(i["item"], {})) for i in items]}


class Check(BaseModel):
    item: str
    done: bool = True
    by: str = "operator"


@app.post("/api/alerts/{alert_id}/checklist", dependencies=[Depends(require_key)])
def checklist_set(alert_id: str, b: Check):
    STORE.check(alert_id, b.item, b.by, b.done)
    ev = STORE.audit({"type": "checklist", "alertId": alert_id, "item": b.item, "done": b.done, "by": b.by})
    _broadcast("hap", ev)
    return checklist(alert_id)


@app.get("/api/advisory/{scope}/{pid:path}")
def advisory(scope: str, pid: str, lang: str = "en", audience: str = "public", lead: int | None = None):
    e = eng()
    if lang not in A.LANGS:
        raise HTTPException(400, f"lang must be one of {list(A.LANGS)}")
    if audience not in A.AUDIENCES:
        raise HTTPException(400, f"audience must be one of {A.AUDIENCES}")
    st = e.states.get(f"{scope}:{pid}")
    if not st:
        raise HTTPException(404, "unknown place")
    days = [st["today"], *st["forecast"]]
    k = lead if lead is not None else (st["target"]["trigger"].get("lead", 0) or 0)
    k = max(0, min(k, len(days) - 1))
    return {"scope": scope, "id": pid, "hapLevel": st["band"], **A.render_advisory(st["name"], st["band"], k, days[k], lang, audience)}


# --------------------------------------------------------------------------- subscriptions
class Sub(BaseModel):
    name: str
    channel: str
    address: str
    language: str = "en"
    audience: str = "public"
    minLevel: int = Field(1, ge=1, le=3)
    wards: list[str] = []
    cities: list[str] = []
    role: str | None = None


@app.get("/api/subscriptions", dependencies=[Depends(require_key)])
def subs():
    return [dict(s, address=s["address"] if s["channel"] == "webhook" else A.mask(s["address"])) for s in STORE.list_subs()]


@app.post("/api/subscriptions", dependencies=[Depends(require_key)], status_code=201)
def subs_add(b: Sub):
    if b.channel not in A.CHANNELS:
        raise HTTPException(400, f"channel must be one of {A.CHANNELS}")
    if b.language not in A.LANGS or b.audience not in A.AUDIENCES:
        raise HTTPException(400, "unknown language or audience")
    if not b.wards and not b.cities:
        raise HTTPException(400, "subscribe to at least one ward or city")
    if b.channel == "webhook" and not b.address.startswith(("http://", "https://")):
        raise HTTPException(400, "webhook address must be an http(s) URL")
    s = STORE.add_sub(b.model_dump())
    return dict(s, address=A.mask(s["address"]))


@app.delete("/api/subscriptions/{sid}", dependencies=[Depends(require_key)], status_code=204)
def subs_del(sid: str):
    if not STORE.remove_sub(sid):
        raise HTTPException(404, "unknown subscription")


@app.get("/api/deliveries", dependencies=[Depends(require_key)])
def deliveries(limit: int = 200):
    return STORE.deliveries(limit)


@app.get("/api/audit")
def audit(limit: int = 200):
    return STORE.audits(limit)


# --------------------------------------------------------------------------- decisions
class Cooling(BaseModel):
    city: str
    n: int = Field(5, ge=0, le=60)
    radiusKm: float = Field(C.COVERAGE_RADIUS_KM, ge=0.3, le=10)
    leads: list[int] = [1, 2, 3]


def _demand(city, leads):
    e = eng()
    pts = []
    for w in e.places.city_wards(city):
        days = e.days_for(w.key, e.as_of)
        ex = sum(max(0.0, days[k]["excessDeaths"] or 0) for k in leads if k < len(days))
        lo = sum(max(0.0, days[k]["excessLow"] or 0) for k in leads if k < len(days))
        hi = sum(max(0.0, days[k]["excessHigh"] or 0) for k in leads if k < len(days))
        # Floor by exposure so centres still go somewhere sensible on a mild day.
        pts.append({"key": w.key, "id": w.id, "name": w.name, "lat": w.lat, "lon": w.lon, "excess": ex, "low": lo,
                    "high": hi, "demand": ex + 1e-6 * w.population * w.vuln_mult, "population": w.population})
    return pts


@app.post("/api/optimize/cooling")
def cooling(b: Cooling):
    _city(b.city)
    pts = _demand(b.city, b.leads)
    out = A.greedy_max_cover(pts, b.n, b.radiusKm)
    f = C.INTERVENTIONS["cooling_centres"]["averted"]
    cov = set(out["covered"])
    cov_ex = sum(p["excess"] for p in pts if p["key"] in cov)
    return {
        "city": b.city, "n": b.n, "radiusKm": b.radiusKm, "leads": b.leads, "asOf": eng().as_of,
        "method": "greedy maximal covering (lazy evaluation); worst case within (1-1/e) of optimal",
        "centres": [{"rank": i + 1, "key": c["site"]["key"], "name": c["site"]["name"], "lat": c["site"]["lat"],
                     "lon": c["site"]["lon"], "newlyCovered": c["newWards"]} for i, c in enumerate(out["chosen"])],
        "coveredWards": len(cov), "totalWards": len(pts), "coveredRiskShare": round(out["coveredShare"], 3),
        "coveredPopulation": round(sum(p["population"] for p in pts if p["key"] in cov)),
        "excessInCovered": round(cov_ex, 3), "excessCity": round(sum(p["excess"] for p in pts), 3),
        "estimatedDeathsAverted": round(cov_ex * f, 3),
        "assumption": f"Cooling-centre access averts {f:.0%} of excess deaths in covered wards (a planning assumption, not an estimate).",
    }


class Counterfactual(BaseModel):
    city: str
    measures: list[str] = []
    coolingCentres: int = Field(0, ge=0, le=60)
    radiusKm: float = C.COVERAGE_RADIUS_KM
    leads: list[int] = [1, 2, 3]


@app.post("/api/counterfactual")
def counterfactual(b: Counterfactual):
    _city(b.city)
    bad = [m for m in b.measures if m not in C.INTERVENTIONS]
    if bad:
        raise HTTPException(400, f"unknown measures {bad}")
    pts = _demand(b.city, b.leads)
    covered = set()
    if b.coolingCentres:
        covered = set(A.greedy_max_cover(pts, b.coolingCentres, b.radiusKm)["covered"])
    city_wide = [m for m in b.measures if m != "cooling_centres"]

    def keep(key):
        k = 1 - C.INTERVENTIONS["cooling_centres"]["averted"] if key in covered else 1.0
        for m in city_wide:
            k *= 1 - C.INTERVENTIONS[m]["averted"]
        return k
    tot = {f: sum(p[f] for p in pts) for f in ("excess", "low", "high")}
    aft = {f: sum(p[f] * keep(p["key"]) for p in pts) for f in ("excess", "low", "high")}
    return {
        "city": b.city, "asOf": eng().as_of, "leads": b.leads, "measures": b.measures, "coolingCentres": b.coolingCentres,
        "coveredWards": len(covered),
        "baseline": {k: round(v, 3) for k, v in tot.items()},
        "withMeasures": {k: round(v, 3) for k, v in aft.items()},
        "averted": {k: round(tot[k] - aft[k], 3) for k in tot},
        "byWard": sorted([{"key": p["key"], "name": p["name"], "before": round(p["excess"], 4),
                           "after": round(p["excess"] * keep(p["key"]), 4)} for p in pts], key=lambda r: -r["before"])[:15],
        "note": "Effects combine multiplicatively, so stacked measures can never avert more than 100%. "
                "Effect sizes are configurable planning assumptions, not estimates from data.",
    }


@app.get("/api/cities/{city}/allocation")
def allocation(city: str, leads: str = "1,2,3"):
    _city(city)
    ls = [int(x) for x in leads.split(",") if x.strip().isdigit()]
    pts = sorted(_demand(city, ls), key=lambda p: -p["excess"])
    tot = sum(p["excess"] for p in pts) or 1
    return {"city": city, "asOf": eng().as_of, "leads": ls,
            "wards": [{"rank": i + 1, "key": p["key"], "name": p["name"], "excess": round(p["excess"], 3),
                       "share": round(p["excess"] / tot, 4), "population": round(p["population"])} for i, p in enumerate(pts)]}


@app.get("/api/cities/{city}/export.csv")
def export_csv(city: str):
    e = eng()
    _city(city)
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["as_of", "mode", "ward_id", "ward", "population", "built_up", "uhi_day_c", "uhi_night_c", "v_index",
                 "cooling_access", "vuln_mult", "hap_level", "lead", "date", "htsi", "q10", "q90", "band", "p_warning",
                 "wbgt_max", "utci_max", "night_wbgt", "tmax", "rr", "mri", "expected_deaths", "excess_deaths",
                 "excess_low", "excess_high"])
    for w in e.places.city_wards(city):
        st = e.states.get(f"ward:{w.key}")
        for d in e.days_for(w.key, e.as_of):
            wr.writerow([e.as_of, e.mode, w.id, w.name, round(w.population), round(w.built, 3), round(w.d_day, 2),
                         round(w.d_night, 2), round(w.v_index, 3), round(w.ac, 3), round(w.vuln_mult, 3),
                         st["band"] if st else "Normal", d["lead"], d["date"], d["htsi"], d["q10"], d["q90"], d["band"],
                         d["pWarning"], d["wbgt"], d["utci"], d["nightWbgt"], d["tmax"], d["rr"], d["mri"],
                         d["expectedDeaths"], d["excessDeaths"], d["excessLow"], d["excessHigh"]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"content-disposition": f'attachment; filename="ushmalens_{city}_{e.as_of}.csv"'})


# --------------------------------------------------------------------------- evidence
@app.get("/api/evidence")
def evidence():
    e = eng()
    calib = json.loads((C.BUILD_DIR / "calibration.json").read_text())
    return {"backtest": e.backtest, "calibration": calib, "riskCurves": {c: cv.public() for c, cv in e.curves.items()},
            "htsiWeights": C.HTSI_WEIGHTS}


@app.get("/api/evidence/history/{city}")
def evidence_history(city: str, start: str | None = None, end: str | None = None):
    import pandas as pd
    h = pd.read_parquet(C.BUILD_DIR / f"history_{_city(city)}.parquet")
    if start:
        h = h[h["date"] >= start]
    if end:
        h = h[h["date"] <= end]
    h = h.assign(date=h["date"].dt.strftime("%Y-%m-%d"))
    cols = ["date", "t2m_max", "wbgt_max", "utci_max", "wbgt_night_min", "htsi", "band", "imd_hw", "anomaly_regime"]
    return {"city": city, "rows": json.loads(h[cols].round(2).to_json(orient="records"))}


# --------------------------------------------------------------------------- field (ASHA) view
@app.get("/api/field/{city}/{wid:path}")
def field(city: str, wid: str, lang: str | None = None):
    e = eng()
    w = _ward(city, wid)
    lang = lang if lang in A.LANGS else CITIES[city]["lang"]
    L = A.LANGS[lang]
    st = e.states.get(f"ward:{w.key}")
    days = e.days_for(w.key, e.as_of)
    level = st["band"] if st else "Normal"
    lead = (st["target"]["trigger"].get("lead", 0) or 0) if st else 0
    adv = A.render_advisory(w.name, level, lead, days[min(lead, len(days) - 1)], lang)
    worst = max(range(min(4, len(days))), key=lambda k: days[k]["htsi"])
    return {
        "key": w.key, "name": w.name, "city": city, "asOf": e.as_of, "mode": e.mode, "lang": lang,
        "level": level, "levelLabel": L["levels"][level],
        "days": [{"date": d["date"], "band": d["band"], "label": L["levels"][d["band"]], "htsi": d["htsi"],
                  "tmax": d["tmax"]} for d in days[:4]],
        "peak": {"date": days[worst]["date"], "band": days[worst]["band"], "label": L["levels"][days[worst]["band"]]},
        "actions": adv["actions"] or L["audiences"]["public"]["Watch"], "sms": adv["sms"],
        "emergency": L.get("emergency"), "review": L["_meta"]["review"],
    }


# --------------------------------------------------------------------------- live stream
@app.get("/api/stream")
async def stream(request: Request):
    q: asyncio.Queue = asyncio.Queue()
    SUBSCRIBERS.add(q)

    async def gen():
        try:
            yield f"event: hello\ndata: {json.dumps({'asOf': eng().as_of, 'mode': eng().mode})}\n\n"
            while not await request.is_disconnected():
                try:
                    yield await asyncio.wait_for(q.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            SUBSCRIBERS.discard(q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"cache-control": "no-cache"})


# --------------------------------------------------------------------------- dashboard
if (C.DASHBOARD_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=C.DASHBOARD_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    f = C.DASHBOARD_DIST / path
    if path and f.is_file():
        return FileResponse(f)
    idx = C.DASHBOARD_DIST / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return HTMLResponse("<h1>UshmaLens API is running</h1><p>Build the dashboard: <code>cd dashboard && npm install && npm run build</code>. "
                        "API docs at <a href='/docs'>/docs</a>.</p>")


@app.exception_handler(Exception)
async def _err(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=500)
