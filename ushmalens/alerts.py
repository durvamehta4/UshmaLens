"""Advisories, CAP 1.2, delivery channels, the operations store and cooling-centre siting.

* **Advisories** -- 7 audiences (USHMA) x 7 languages: English, Hindi and Odia
  from USHMA; Marathi, Gujarati, Tamil and Telugu built from TAPAS's translated
  resident guidance and heat-stroke emergency block. Non-English text is marked
  as a draft pending native-speaker review; specialist audiences fall back to
  English in the four new languages rather than shipping machine guesses.
* **CAP 1.2** -- one <info> block per language. Replay and drill alerts are
  status "Exercise" and can never be mistaken for a live warning downstream.
* **Channels** -- simulated (default; logged exactly as it would be sent),
  Twilio SMS / WhatsApp, or HMAC-signed webhooks.
* **Store** -- SQLite: subscriptions, deliveries (one message per alert per
  subscriber), acknowledgements, checklist, audit trail.
* **Cooling centres** -- greedy maximal covering over forecast excess deaths.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ushmalens import config as C
from ushmalens.places import haversine_km

I18N_DIR = Path(__file__).parent / "i18n"
LANGS = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(I18N_DIR.glob("*.json"))}
LANG_ORDER = ["en", "hi", "mr", "gu", "ta", "te", "or"]
LANGS = {k: LANGS[k] for k in LANG_ORDER if k in LANGS}
AUDIENCES = list(LANGS["en"]["audiences"])
DIGITS = {"or": "୦୧୨୩୪୫୬୭୮୯", "mr": "०१२३४५६७८९", "gu": "૦૧૨૩૪૫૬૭૮૯"}
LANG_CAP = {"en": "en-IN", "hi": "hi-IN", "mr": "mr-IN", "gu": "gu-IN", "ta": "ta-IN", "te": "te-IN", "or": "or-IN"}


# =========================================================================== advisories
def _digits(s, lang):
    d = DIGITS.get(lang)
    return re.sub(r"[0-9]", lambda m: d[int(m.group())], str(s)) if d else str(s)


def _fill(t, v, lang):
    def rep(m):
        x = v.get(m.group(1))
        if x is None:
            return ""
        return _digits(x, lang) if isinstance(x, (int, float)) else str(x)
    return re.sub(r"\{(\w+)\}", rep, t)


def _num(x, d=0):
    if x is None:
        return None
    return int(round(x)) if d == 0 else round(float(x), d)


def render_advisory(place: str, level: str, lead: int, day: dict, lang: str = "en", audience: str = "public") -> dict:
    L = LANGS.get(lang, LANGS["en"])
    en = LANGS["en"]
    aud_l = L["audiences"].get(audience)
    fell_back = aud_l is None
    aud = aud_l or en["audiences"].get(audience) or en["audiences"]["public"]
    t = L["days"]
    dayp = t["today"] if lead == 0 else t["tomorrow"] if lead == 1 else _fill(t["onDate"], {"date": _digits(day.get("date", ""), lang)}, lang)
    v = {"place": place, "day": dayp, "htsi": _num(day.get("htsi")), "wbgt": _num(day.get("wbgt"), 1),
         "night": _num(day.get("nightWbgt"), 1), "level": L["levels"].get(level, level),
         "excess": _num(day.get("excessDeaths"), 1), "excessLow": _num(day.get("excessLow"), 1),
         "excessHigh": _num(day.get("excessHigh"), 1), "ed": _num(day.get("edSurge"))}
    act = level != "Normal"
    actions = [_fill(a, v, lang if not fell_back else "en") for a in aud.get(level, [])] if act else []
    return {
        "language": lang, "languageName": L["_meta"]["language"], "reviewStatus": L["_meta"]["review"],
        "audience": audience, "audienceLabel": aud.get("label", audience), "audienceFallback": fell_back,
        "level": level, "levelLabel": L["levels"].get(level, level),
        "headline": _fill(L["headline"][level], v, lang) if act else None,
        "sms": _fill(L["sms"][level], v, lang) if act else None,
        "description": _fill(L["description"], v, lang),
        "actions": actions,
        "emergency": L.get("emergency") if level in ("Warning", "Emergency") else None,
    }


# =========================================================================== CAP 1.2
def _esc(s):
    return (str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def ist_now() -> str:
    t = datetime.now(timezone.utc) + timedelta(hours=5.5)
    return t.strftime("%Y-%m-%dT%H:%M:%S+05:30")


SEVERITY = {"Watch": "Moderate", "Warning": "Severe", "Emergency": "Extreme"}
RESPONSE = {"Watch": "Prepare", "Warning": "Avoid", "Emergency": "Shelter"}


def _cap_polygon(geom):
    ring = geom["coordinates"][0][0] if geom["type"] == "MultiPolygon" else geom["coordinates"][0]
    while ring and isinstance(ring[0][0], list):   # tolerate an extra nesting level
        ring = ring[0]
    pts = [f"{p[1]:.5f},{p[0]:.5f}" for p in ring]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return " ".join(pts)


def build_cap(alert_id, level, lead, day, place, geometry, area_code, actual: bool, langs: list[str]) -> str:
    sent = ist_now()
    status = "Actual" if actual else "Exercise"
    urgency = "Immediate" if lead <= 0 else "Expected" if lead <= 2 else "Future"
    certainty = "Observed" if lead <= 0 else "Likely" if (day.get("pWarning") or 0) >= 0.5 else "Possible"
    infos = []
    for lang in langs:
        a = render_advisory(place, level, lead, day, lang)
        infos.append(f"""  <info>
    <language>{LANG_CAP.get(lang, lang)}</language>
    <category>Met</category>
    <category>Health</category>
    <event>{_esc(a['levelLabel'])}</event>
    <responseType>{RESPONSE.get(level, 'Monitor')}</responseType>
    <urgency>{urgency}</urgency>
    <severity>{SEVERITY.get(level, 'Minor')}</severity>
    <certainty>{certainty}</certainty>
    <eventCode><valueName>UshmaLens</valueName><value>HEAT-{level.upper()}</value></eventCode>
    <effective>{sent}</effective>
    <onset>{day['date']}T00:00:00+05:30</onset>
    <expires>{day['date']}T23:59:59+05:30</expires>
    <senderName>{_esc(C.CAP_SENDER_NAME)}</senderName>
    <headline>{_esc(a['headline'])}</headline>
    <description>{_esc(a['description'])}</description>
    <instruction>{_esc(' '.join(a['actions']))}</instruction>
    <parameter><valueName>HTSI</valueName><value>{day.get('htsi', '')}</value></parameter>
    <parameter><valueName>HTSI_80PCT_RANGE</valueName><value>{day.get('q10', '')}-{day.get('q90', '')}</value></parameter>
    <parameter><valueName>P_WARNING</valueName><value>{day.get('pWarning', '')}</value></parameter>
    <parameter><valueName>MRI</valueName><value>{day.get('mri', '')}</value></parameter>
    <parameter><valueName>WBGT_MAX_C</valueName><value>{day.get('wbgt', '')}</value></parameter>
    <area>
      <areaDesc>{_esc(place)}</areaDesc>
{'      <polygon>' + _cap_polygon(geometry) + '</polygon>' + chr(10) if geometry else ''}      <geocode><valueName>UshmaLens_AREA</valueName><value>{_esc(area_code)}</value></geocode>
    </area>
  </info>""")
    note = "" if actual else "  <note>Replay or drill generated by UshmaLens for demonstration and exercises. Not a live warning.</note>\n"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>{_esc(alert_id)}</identifier>
  <sender>{_esc(C.CAP_SENDER)}</sender>
  <sent>{sent}</sent>
  <status>{status}</status>
  <msgType>Alert</msgType>
  <scope>Public</scope>
{note}{chr(10).join(infos)}
</alert>
"""


# =========================================================================== channels
CHANNELS = ["console", "sms", "whatsapp", "webhook"]


def mask(a: str) -> str:
    return "*" * (len(a) - 4) + a[-4:] if a and len(a) > 4 else a


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(C.WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def send(channel: str, to: str, message: str, payload: dict) -> dict:
    simulated = C.CHANNEL_MODE != "twilio" and channel != "webhook"
    try:
        if channel == "console" or simulated:
            return {"status": "simulated", "channel": channel, "to": mask(to), "chars": len(message or "")}
        if channel == "webhook":
            body = json.dumps(payload, ensure_ascii=False).encode()
            r = httpx.post(to, content=body, timeout=8,
                           headers={"content-type": "application/json", "x-ushmalens-signature": sign(body)})
            return {"status": "sent" if r.is_success else "failed", "channel": channel, "to": to, "providerStatus": r.status_code}
        t = C.TWILIO
        if not t["sid"] or not t["token"]:
            raise RuntimeError("Twilio credentials not configured")
        frm = f"whatsapp:{t['whatsapp_from']}" if channel == "whatsapp" else t["sms_from"]
        dest = f"whatsapp:{to}" if channel == "whatsapp" else to
        r = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{t['sid']}/Messages.json",
                       auth=(t["sid"], t["token"]), data={"From": frm, "To": dest, "Body": message}, timeout=10)
        return {"status": "sent" if r.is_success else "failed", "channel": channel, "to": mask(to),
                "providerStatus": r.status_code}
    except Exception as e:  # noqa: BLE001 - a failed send is recorded, never raised
        return {"status": "failed", "channel": channel, "to": mask(to), "error": str(e)}


# =========================================================================== store
class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or (C.RUNTIME_DIR / "ushmalens.sqlite")
        self.lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        create table if not exists subs (id text primary key, body text not null, created text);
        create table if not exists deliveries (id integer primary key autoincrement, alert_id text, sub_id text,
            at text, body text);
        create table if not exists audit (id integer primary key autoincrement, at text, body text);
        create table if not exists checklist (alert_id text, item text, by text, at text, primary key(alert_id, item));
        """)
        if not self.list_subs():
            self._seed()

    def _seed(self):
        demo = [
            {"name": "Ahmedabad Municipal Corporation control room", "role": "municipal", "channel": "console",
             "address": "amc-control", "language": "gu", "audience": "municipal", "minLevel": 1, "cities": ["Ahmedabad"]},
            {"name": "Civil Hospital Ahmedabad duty officer", "role": "hospital", "channel": "sms",
             "address": "+910000000001", "language": "en", "audience": "hospitals", "minLevel": 2, "cities": ["Ahmedabad"]},
            {"name": "BMC disaster cell", "role": "municipal", "channel": "whatsapp", "address": "+910000000002",
             "language": "mr", "audience": "public", "minLevel": 1, "cities": ["Mumbai"]},
            {"name": "GCC ward 0 ASHA group", "role": "asha", "channel": "whatsapp", "address": "+910000000003",
             "language": "ta", "audience": "public", "minLevel": 1, "wards": ["Chennai:0"]},
            {"name": "GHMC zonal office", "role": "municipal", "channel": "sms", "address": "+910000000004",
             "language": "te", "audience": "public", "minLevel": 2, "cities": ["Hyderabad"]},
            {"name": "Power utility load desk (webhook)", "role": "utility", "channel": "console", "address": "load-desk",
             "language": "en", "audience": "power_utility", "minLevel": 2,
             "cities": ["Ahmedabad", "Mumbai", "Chennai", "Hyderabad"]},
        ]
        for d in demo:
            self.add_sub(d)

    def _now(self):
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def add_sub(self, d: dict) -> dict:
        d = {"id": uuid.uuid4().hex[:10], "wards": [], "cities": [], **d}
        with self.lock:
            self.db.execute("insert into subs values (?,?,?)", (d["id"], json.dumps(d, ensure_ascii=False), self._now()))
            self.db.commit()
        return d

    def list_subs(self) -> list[dict]:
        return [json.loads(r["body"]) for r in self.db.execute("select body from subs order by created")]

    def remove_sub(self, sid: str) -> bool:
        with self.lock:
            n = self.db.execute("delete from subs where id=?", (sid,)).rowcount
            self.db.commit()
        return n > 0

    def delivered(self, alert_id, sub_id) -> bool:
        return self.db.execute("select 1 from deliveries where alert_id=? and sub_id=?", (alert_id, sub_id)).fetchone() is not None

    def log_delivery(self, alert_id, sub_id, body: dict):
        with self.lock:
            self.db.execute("insert into deliveries (alert_id, sub_id, at, body) values (?,?,?,?)",
                            (alert_id, sub_id, self._now(), json.dumps(body, ensure_ascii=False)))
            self.db.commit()

    def deliveries(self, limit=200) -> list[dict]:
        return [dict(json.loads(r["body"]), at=r["at"]) for r in
                self.db.execute("select at, body from deliveries order by id desc limit ?", (limit,))]

    def audit(self, body: dict) -> dict:
        body = dict(body, at=self._now())
        with self.lock:
            cur = self.db.execute("insert into audit (at, body) values (?,?)", (body["at"], json.dumps(body, ensure_ascii=False)))
            self.db.commit()
        body["id"] = cur.lastrowid
        return body

    def audits(self, limit=200) -> list[dict]:
        return [dict(json.loads(r["body"]), id=r["id"]) for r in
                self.db.execute("select id, body from audit order by id desc limit ?", (limit,))]

    def check(self, alert_id, item, by, done: bool):
        with self.lock:
            if done:
                self.db.execute("insert or replace into checklist values (?,?,?,?)", (alert_id, item, by, self._now()))
            else:
                self.db.execute("delete from checklist where alert_id=? and item=?", (alert_id, item))
            self.db.commit()

    def checked(self, alert_id) -> dict:
        return {r["item"]: {"by": r["by"], "at": r["at"]} for r in
                self.db.execute("select * from checklist where alert_id=?", (alert_id,))}


# =========================================================================== dispatch
def dispatch(engine, store: Store, dry_run: bool = False) -> list[dict]:
    """Send each active alert to each matching subscriber, once per alert."""
    results = []
    actual = engine.mode == "live" and not engine.drill
    for s in engine.active():
        rec = engine.alert_record(s["alertId"])
        for sub in store.list_subs():
            wants = ((s["scope"] == "ward" and s["id"] in sub.get("wards", []))
                     or (s["scope"] == "city" and s["city"] in sub.get("cities", [])))
            if not wants or s["level"] < sub.get("minLevel", 1):
                continue
            if store.delivered(s["alertId"], sub["id"]):
                continue
            adv = render_advisory(s["name"], s["band"], rec["lead"], rec["day"], sub.get("language", "en"),
                                  sub.get("audience", "public"))
            msg = adv["sms"] or ""
            if sub["channel"] == "whatsapp" and adv["actions"]:
                msg += "\n\n" + "\n".join("• " + a for a in adv["actions"])
            if not actual:
                msg = "[EXERCISE] " + msg
            payload = {"alertId": s["alertId"], "place": s["name"], "scope": s["scope"], "level": s["band"],
                       "asOf": engine.as_of, "mode": engine.mode, "status": "Actual" if actual else "Exercise",
                       "advisory": adv, "day": rec["day"]}
            res = {"status": "dry-run", "channel": sub["channel"]} if dry_run else send(sub["channel"], sub["address"], msg, payload)
            row = {"alertId": s["alertId"], "subscriber": sub["name"], "place": s["name"], "level": s["band"],
                   "language": sub.get("language"), "message": msg, **res}
            if not dry_run:
                store.log_delivery(s["alertId"], sub["id"], row)
            results.append(row)
    return results


# =========================================================================== cooling centres
def greedy_max_cover(points: list[dict], n: int, radius_km: float) -> dict:
    """Pick ``n`` sites (from the points themselves) maximising covered demand.

    Lazy greedy: within (1 - 1/e) of optimal in the worst case, instant here.
    """
    if n <= 0:
        return {"chosen": [], "covered": [], "coveredShare": 0.0}
    covers = [[i for i, p in enumerate(points) if haversine_km(s["lat"], s["lon"], p["lat"], p["lon"]) <= radius_km]
              for s in points]
    covered = [False] * len(points)
    chosen = []

    def gain(j):
        return sum(points[i]["demand"] for i in covers[j] if not covered[i])

    heap = sorted(((gain(j), j) for j in range(len(points))), reverse=True)
    while len(chosen) < min(n, len(points)) and heap:
        g0, j = heap.pop(0)
        g = gain(j)
        if heap and g < heap[0][0]:
            heap.append((g, j))
            heap.sort(reverse=True)
            continue
        if g <= 0 and chosen:
            break
        chosen.append({"site": points[j], "gain": g, "newWards": [points[i]["key"] for i in covers[j] if not covered[i]]})
        for i in covers[j]:
            covered[i] = True
    tot = sum(p["demand"] for p in points) or 1
    return {"chosen": chosen, "covered": [p["key"] for p, c in zip(points, covered) if c],
            "coveredShare": sum(p["demand"] for p, c in zip(points, covered) if c) / tot}
