"""Heat Action Plan state machine: Normal -> Watch -> Warning -> Emergency.

Ported from USHMA (server/src/alerts/hap.js) and driven here by *ensemble*
exceedance probabilities instead of single-model classifiers.

Two rules make this an early-warning system rather than a thermometer:

1. **It acts on the forecast.** Today's target level is the worst of today's
   band and the D+1..D+2 forecast -- either the median crossing a band edge, or
   the probability of crossing it clearing a trigger. A likely Warning anywhere
   in D+3..D+5 raises at least a Watch. Opening cooling centres and moving work
   hours take days to organise.
2. **It does not flap.** Escalation is immediate; de-escalation is one level at
   a time, only after the level has been held ``min_dwell_days`` *and* HTSI has
   stayed below the level's entry edge minus a margin for as many consecutive
   days. A system that goes red, green, red trains people to ignore it.

Every transition carries its reason and evidence, so the audit trail answers
"why did we open cooling centres on the 28th?". (TAPAS's cooldown/digest rules
live on in the dispatcher: one message per alert per subscriber.)
"""

from __future__ import annotations

from datetime import date

from ushmalens import config as C

LEVELS = list(C.BANDS)


def level_of(band: str) -> int:
    return LEVELS.index(band) if band in LEVELS else 0


def _f(v, d=1):
    return "n/a" if v is None else f"{v:.{d}f}"


def _pct(p):
    return f"{round((p or 0) * 100)}%"


def target_level(days: list[dict], cfg: dict | None = None) -> dict:
    cfg = cfg or C.HAP
    d0 = days[0]
    level = level_of(d0["band"])
    reason = f"Observed {d0['band']} today (HTSI {_f(d0['htsi'])})"
    trigger = {"lead": 0, "htsi": d0["htsi"], "band": d0["band"]}

    for k in range(1, min(cfg["anticipatory_lead_days"], len(days) - 1) + 1):
        d = days[k]
        lv = level_of(d["band"])
        why = f"Forecast {d['band']} on D+{k} (HTSI {_f(d['htsi'])}, 80% range {_f(d.get('q10'))}-{_f(d.get('q90'))})"
        if (d.get("pEmergency") or 0) >= cfg["p_warning_trigger"] and lv < 3:
            lv, why = 3, f"Emergency-level heat likely on D+{k}: P = {_pct(d['pEmergency'])} (trigger {_pct(cfg['p_warning_trigger'])})"
        elif (d.get("pWarning") or 0) >= cfg["p_warning_trigger"] and lv < 2:
            lv, why = 2, f"Warning-level heat likely on D+{k}: P = {_pct(d['pWarning'])} (trigger {_pct(cfg['p_warning_trigger'])})"
        elif (d.get("pWatch") or 0) >= cfg["p_watch_trigger"] and lv < 1:
            lv, why = 1, f"Watch-level heat likely on D+{k}: P = {_pct(d['pWatch'])} (trigger {_pct(cfg['p_watch_trigger'])})"
        if lv > level:
            level, reason = lv, why
            trigger = {"lead": k, "htsi": d["htsi"], "band": d["band"], "pWarning": d.get("pWarning")}

    if level < 1:
        for k in range(cfg["anticipatory_lead_days"] + 1, min(cfg["watch_lead_days"], len(days) - 1) + 1):
            if (days[k].get("pWarning") or 0) >= cfg["p_heads_up"]:
                level = 1
                reason = f"Warning-level heat possible on D+{k} (P = {_pct(days[k]['pWarning'])}): heads-up Watch"
                trigger = {"lead": k, "htsi": days[k]["htsi"], "band": days[k]["band"], "pWarning": days[k].get("pWarning")}
                break
    return {"level": level, "band": LEVELS[level], "reason": reason, "trigger": trigger}


def _days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def step(prev: dict, target: dict, htsi_today: float, on: str, cfg: dict | None = None):
    cfg = cfg or C.HAP
    if target["level"] > prev["level"]:
        return ({"level": target["level"], "since": on, "below": 0},
                {"date": on, "from": LEVELS[prev["level"]], "to": target["band"], "kind": "escalate",
                 "reason": target["reason"], "trigger": target["trigger"]})
    if target["level"] < prev["level"]:
        entry = C.BAND_EDGES[prev["level"] - 1]
        clear = htsi_today is not None and htsi_today < entry - cfg["deescalation_margin"]
        below = prev["below"] + 1 if clear else 0
        held = _days_between(prev["since"], on) >= cfg["min_dwell_days"]
        if below >= cfg["min_dwell_days"] and held:
            to = prev["level"] - 1
            return ({"level": to, "since": on, "below": 0},
                    {"date": on, "from": LEVELS[prev["level"]], "to": LEVELS[to], "kind": "de-escalate",
                     "reason": f"HTSI below {_f(entry - cfg['deescalation_margin'])} for {below} consecutive days",
                     "trigger": {"lead": 0, "htsi": htsi_today}})
        return dict(prev, below=below), None
    return dict(prev, below=0), None


def run_sequence(sequence: list[dict], cfg: dict | None = None) -> dict:
    """``sequence``: [{date, days:[D0..D5]}] in date order for one place."""
    state, transitions, target = None, [], None
    for item in sequence:
        if state is None:
            state = {"level": 0, "since": item["date"], "below": 0}
        target = target_level(item["days"], cfg)
        state, tr = step(state, target, item["days"][0]["htsi"], item["date"], cfg)
        if tr:
            transitions.append(tr)
    return {"state": state, "transitions": transitions, "target": target}
