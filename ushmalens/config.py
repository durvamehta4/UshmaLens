"""Central configuration for UshmaLens.

Every threshold, weight and planning assumption a reviewer might challenge lives
on this page, with the reason it has the value it has. Environment variables
override deployment settings (ports, keys, channels); model constants are edited
here or, for the ones exposed at runtime, through ``POST /api/settings``.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CITY_DIR = DATA / "cities"
ERA5_DIR = DATA / "era5"
BUILD_DIR = DATA / "build"          # derived artefacts (climatology, backtest, model)
RUNTIME_DIR = DATA / "runtime"      # caches, sqlite, outbox
DASHBOARD_DIST = ROOT / "dashboard" / "dist"

for _d in (ERA5_DIR, BUILD_DIR, RUNTIME_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _env(name: str, default):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return type(default)(v) if not isinstance(default, bool) else v.lower() in ("1", "true", "yes")


# --------------------------------------------------------------------------- server
HOST = _env("USHMALENS_HOST", "127.0.0.1")
PORT = _env("USHMALENS_PORT", 8000)
#: Write routes (dispatch, subscriptions, clock) need this in ``x-api-key``.
#: The default exists so a local demo works; the server warns while it is in use.
API_KEY = _env("USHMALENS_API_KEY", "ushmalens-dev-key")
#: Background refresh of the live forecast, seconds. Open-Meteo is free but
#: rate-limited per IP; one refresh pulls a handful of batched requests.
LIVE_REFRESH_S = _env("USHMALENS_LIVE_REFRESH_S", 3 * 3600)
NO_SCHEDULER = _env("USHMALENS_NOSCHED", False)

# --------------------------------------------------------------------------- time
#: India has one time zone and no DST.
IST_OFFSET_H = 5.5
NIGHT_START_H = 22      # night window for the recovery deficit, local clock
NIGHT_END_H = 6

# --------------------------------------------------------------------------- HTSI
#: Human Thermal Stress Index (0-100) channel weights. Inherited from USHMA, where
#: they were revised so that absolute physiological load leads and the relative
#: channels amplify it (a pleasant-but-unusual October day must not outrank a
#: 42 degC May day). See docs/METHODS.md.
HTSI_WEIGHTS = {"intensity": 0.50, "anomaly": 0.20, "night_relief": 0.20, "duration": 0.10}
#: Overnight minimum WBGT above which physiological recovery is impaired.
NIGHT_RELIEF_THRESHOLD_C = 26.0
#: Overnight minimum air temperature above which a dry night counts as
#: impaired recovery (the dry-heat route of the same channel).
NIGHT_AIR_THRESHOLD_C = 29.0
DURATION_SATURATION_DAYS = 5
#: Anomaly/duration only count once it is genuinely hot (daily-max WBGT ramps
#: the gate from 0 at 27 degC to 1 at 31 degC).
HTSI_GATE_WBGT = (27.0, 31.0)
#: Band edges for Normal | Watch | Warning | Emergency. Calibrated by USHMA on
#: the March-June distribution in Odisha (top ~10% / 3% / 1% of summer days);
#: data/build/backtest.json reports how often each fires in each city here.
BAND_EDGES = (48.5, 59.5, 63.5)
#: ...and recalibrated by the same rule on the ward-level (UHI-adjusted)
#: March-June distribution of the four pilot cities by scripts/build_history.py.
try:
    import json as _json
    BAND_EDGES = tuple(_json.loads((BUILD_DIR / "calibration.json").read_text())["bandEdges"])
except (OSError, ValueError, KeyError):
    pass
BANDS = ("Normal", "Watch", "Warning", "Emergency")

# --------------------------------------------------------------------------- UHI
#: Grid-to-ward urban heat island uplift (degC), USHMA's parameterisation, now
#: driven by *measured* ward built-up share (satellite + WUDAPT LCZ) and nudged by
#: the ward's MODIS land-surface-temperature anomaly.
UHI = {"a_day": 0.30, "b_day": 1.55, "a_night": 0.50, "b_night": 3.00,
       "wbgt_per_degc": 0.55, "lst_gain": 0.25, "lst_cap": 1.0}

# --------------------------------------------------------------------------- risk
#: Crude death rate per 1000 per year used as the baseline. Approximate SRS
#: urban figures; replace with the municipal registrar's figure when known.
CDR_PER_1000 = {"Mumbai": 5.6, "Ahmedabad": 5.8, "Chennai": 6.4, "Hyderabad": 5.5}
POP_GROWTH_SINCE_2011 = 1.12
#: Effect modification of log-RR by ward vulnerability.
VULN_MULT_RANGE = (0.75, 1.35)
#: Heat-related ED attendances per excess death (assumption, carried as range).
ED_PER_EXCESS_DEATH = (8.0, 15.0, 25.0)

# --------------------------------------------------------------------------- HAP
HAP = {
    "min_dwell_days": 2,           # hold a level at least this long
    "deescalation_margin": 1.0,    # HTSI must fall this far below the entry edge
    "anticipatory_lead_days": 2,   # D+1..D+2 forecast can raise today's level
    "watch_lead_days": 5,          # D+3..D+5 likely Warning raises at most a Watch
    "p_warning_trigger": 0.6,      # ensemble probability that raises Warning
    "p_watch_trigger": 0.6,        # ensemble probability that raises Watch
    "p_heads_up": 0.5,             # D+3..D+5 probability of Warning for a Watch
}

# --------------------------------------------------------------------------- interventions
#: Planning assumptions for the counterfactual panel. ASSUMPTIONS, not estimates.
INTERVENTIONS = {
    "cooling_centres": {"label": "Open cooling centres (covered wards)", "averted": 0.15},
    "work_hours": {"label": "Shift outdoor work out of 11:00-16:00", "averted": 0.08},
    "advisories": {"label": "Targeted SMS/WhatsApp to vulnerable households", "averted": 0.05},
    "water_points": {"label": "Drinking-water points & ORS at hubs", "averted": 0.04},
    "welfare_checks": {"label": "Welfare checks on elderly living alone", "averted": 0.06},
    "cool_roofs": {"label": "Cool-roof / shade programme (seasonal)", "averted": 0.05},
}
COVERAGE_RADIUS_KM = 1.5

# --------------------------------------------------------------------------- channels
CHANNEL_MODE = _env("USHMALENS_CHANNEL_MODE", "simulated")   # or "twilio"
TWILIO = {
    "sid": os.environ.get("TWILIO_ACCOUNT_SID"),
    "token": os.environ.get("TWILIO_AUTH_TOKEN"),
    "sms_from": os.environ.get("TWILIO_SMS_FROM"),
    "whatsapp_from": os.environ.get("TWILIO_WHATSAPP_FROM"),
}
WEBHOOK_SECRET = _env("USHMALENS_WEBHOOK_SECRET", "ushmalens-webhook-dev-secret")
CAP_SENDER = _env("USHMALENS_CAP_SENDER", "alerts@ushmalens-demo.example.org")
CAP_SENDER_NAME = _env("USHMALENS_CAP_SENDER_NAME", "UshmaLens Heat-Health Early Warning (demonstration)")
