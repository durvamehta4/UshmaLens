"""System tests: HTSI behaviour, risk model, HAP state machine, advisories, CAP and the API.

Run offline: replay mode needs only the shipped ERA5 and build artefacts; the
live scheduler is disabled.
"""

from __future__ import annotations

import os
import xml.dom.minidom

import numpy as np
import pandas as pd
import pytest

os.environ["USHMALENS_NOSCHED"] = "1"

from ushmalens import config as C  # noqa: E402
from ushmalens import hap, risk  # noqa: E402
from ushmalens import science as S  # noqa: E402
from ushmalens.alerts import LANGS, build_cap, greedy_max_cover, render_advisory  # noqa: E402

KEY = {"x-api-key": C.API_KEY}


# --------------------------------------------------------------------------- HTSI
@pytest.fixture(scope="module")
def hyd():
    return pd.read_parquet(C.BUILD_DIR / "history_Hyderabad.parquet")


def test_band_edges_are_ordered_and_calibrated():
    e = C.BAND_EDGES
    assert e[0] < e[1] < e[2]
    assert 40 < e[0] < 80


def test_dry_heat_registers_as_anomalous(hyd):
    """Hyderabad's lethal May 2015 heatwave must rank as unusual heat (the
    WBGT-only anomaly channel inherited from Odisha ranked it ordinary)."""
    d = hyd[(hyd["date"] >= "2015-05-20") & (hyd["date"] <= "2015-05-31")]
    assert (d["anomaly_regime"] == "dry").mean() > 0.5
    assert d["pct_rank"].max() >= 90


def test_pre_monsoon_dominates_alerts():
    """In every city most Warning+ days fall in March-June, not the monsoon."""
    import json
    bt = json.loads((C.BUILD_DIR / "backtest.json").read_text())
    for city, b in bt["cities"].items():
        months = b["byMonth"]
        pre = sum(months.get(str(m), {}).get("warning", 0) for m in (3, 4, 5, 6))
        tot = sum(v["warning"] for v in months.values())
        if tot:
            assert pre / tot >= 0.6, city


def test_uhi_raises_ward_htsi(hyd):
    clim = __import__("json").loads((C.BUILD_DIR / "climatology.json").read_text())["Hyderabad"]
    raw = pd.read_parquet(C.ERA5_DIR / "Hyderabad.parquet")
    raw["ts_utc"] = pd.to_datetime(raw["ts_utc"])
    raw = raw[(raw["ts_utc"] >= "2024-04-20") & (raw["ts_utc"] < "2024-05-10")]
    hr = S.hourly_indices(raw, 17.385, 78.4867)
    hr["unit"] = "x"
    d = S.daily_aggregate(hr)
    d = d[d["complete"]]
    m = S.htsi_matrix(d, clim, [0.0, 1.5], [0.0, 3.0])
    assert (m["htsi"][1] >= m["htsi"][0] - 1e-9).all()
    assert m["htsi"][1].mean() > m["htsi"][0].mean()


def test_hourly_wbgt_is_physically_plausible(hyd):
    """Real ERA5 through Liljegren: the tail stays physical.

    A handful of days per city reach 40-41 degC (single humid, sunny hours in
    reanalysis); the 99.9th percentile must stay below 40.5 degC.
    """
    assert hyd["wbgt_max"].quantile(0.999) < 40.5
    assert hyd["wbgt_max"].max() < 42
    assert hyd["wbgt_max"].quantile(0.5) > 20


# --------------------------------------------------------------------------- risk
def test_risk_is_zero_below_threshold_and_monotonic():
    curves = risk.load_curves(None)
    cv = curves["Ahmedabad"]
    h = np.array([[30, 30, 30, 30, 30], [30, 30, 30, 70, 80]], dtype=float)
    out = risk.ward_risk(h, [1e5, 1e5], [1.0, 1.0], "Ahmedabad", cv)
    assert np.allclose(out["excess_central"][0], 0)
    assert out["excess_central"][1, 4] > out["excess_central"][1, 3] > 0
    assert (out["excess_low"] <= out["excess_central"] + 1e-12).all()
    assert (out["excess_central"] <= out["excess_high"] + 1e-12).all()


def test_vulnerability_raises_risk():
    cv = risk.load_curves(None)["Mumbai"]
    h = np.full((2, 5), 75.0)
    out = risk.ward_risk(h, [1e5, 1e5], [0.8, 1.3], "Mumbai", cv)
    assert out["rr_central"][1, -1] > out["rr_central"][0, -1]


def test_curves_use_published_anchors():
    curves = risk.load_curves(None)
    assert curves["Ahmedabad"].rr[1] == pytest.approx(1.249)
    assert curves["Mumbai"].rr[1] == pytest.approx(1.034)


# --------------------------------------------------------------------------- HAP
def _days(htsis, p_warn=None):
    out = []
    for k, h in enumerate(htsis):
        out.append({"lead": k, "htsi": h, "band": S.band_of([h])[0], "q10": h - 3, "q90": h + 3,
                    "pWatch": float(h >= C.BAND_EDGES[0]), "pWarning": (p_warn or {}).get(k, float(h >= C.BAND_EDGES[1])),
                    "pEmergency": float(h >= C.BAND_EDGES[2])})
    return out


def test_hap_escalates_on_forecast_probability():
    t = hap.target_level(_days([40, 45, 45, 45, 45, 45], p_warn={1: 0.8}))
    assert t["band"] == "Warning" and t["trigger"]["lead"] == 1


def test_hap_heads_up_watch_for_distant_warning():
    t = hap.target_level(_days([40, 40, 40, 40, 45, 45], p_warn={4: 0.55}))
    assert t["band"] == "Watch"


def test_hap_does_not_flap():
    warn = C.BAND_EDGES[1] + 2
    seq_h = [warn, warn, 40, warn, 40, 40, 40, 40]
    seq = [{"date": f"2024-05-{10 + i:02d}", "days": _days([h, 40, 40, 40, 40, 40])} for i, h in enumerate(seq_h)]
    res = hap.run_sequence(seq)
    kinds = [t["kind"] for t in res["transitions"]]
    # one escalation, then step-downs only after the dwell -- never back up and down daily
    assert kinds[0] == "escalate"
    assert kinds.count("escalate") <= 2
    assert res["state"]["level"] < 2


# --------------------------------------------------------------------------- advisories, CAP, optimiser
DAY = {"date": "2024-05-21", "htsi": 80.2, "wbgt": 34.1, "nightWbgt": 27.0, "excessDeaths": 1.2, "excessLow": 0.8,
       "excessHigh": 1.9, "edSurge": 18, "q10": 75, "q90": 85, "pWarning": 0.9, "mri": 40}


@pytest.mark.parametrize("lang", list(LANGS))
def test_advisory_renders_every_language(lang):
    a = render_advisory("Ward X", "Emergency", 1, DAY, lang)
    assert a["sms"] and a["headline"] and a["actions"]
    assert "{" not in a["sms"]
    assert a["emergency"]


def test_normal_level_sends_nothing():
    a = render_advisory("Ward X", "Normal", 0, DAY, "en")
    assert a["sms"] is None and a["actions"] == []


def test_cap_is_valid_and_exercise_by_default():
    geom = {"type": "Polygon", "coordinates": [[[72.5, 23.0], [72.6, 23.0], [72.6, 23.1], [72.5, 23.0]]]}
    x = build_cap("UL-1", "Warning", 1, DAY, "Ward X", geom, "ward:X", actual=False, langs=["en", "gu"])
    doc = xml.dom.minidom.parseString(x.encode())
    assert doc.getElementsByTagName("status")[0].firstChild.data == "Exercise"
    assert len(doc.getElementsByTagName("info")) == 2
    assert "23.00000,72.50000" in x


def test_greedy_cover_prefers_demand():
    pts = [{"key": "a", "lat": 23.0, "lon": 72.5, "demand": 10}, {"key": "b", "lat": 23.001, "lon": 72.5, "demand": 5},
           {"key": "c", "lat": 23.5, "lon": 72.5, "demand": 1}]
    out = greedy_max_cover(pts, 1, 1.0)
    assert set(out["covered"]) == {"a", "b"}
    assert greedy_max_cover(pts, 0, 1.0)["covered"] == []


# --------------------------------------------------------------------------- API (replay, offline)
@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient

    from ushmalens import alerts as A
    from ushmalens import api

    with TestClient(api.app) as c:
        api.STORE = A.Store(tmp_path_factory.mktemp("db") / "t.sqlite")
        yield c


def test_api_reads(client):
    for u in ["/api/health", "/api/meta", "/api/clock", "/api/episodes", "/api/overview", "/api/evidence",
              "/api/cities/Mumbai/snapshot?lead=2", "/api/wards/Mumbai/F/N", "/api/alerts/active",
              "/api/field/Chennai/0", "/api/cities/Hyderabad/allocation", "/api/cities/Chennai/export.csv"]:
        assert client.get(u).status_code == 200, u


def test_api_write_routes_need_key(client):
    assert client.post("/api/clock/asof", json={"date": "2024-05-20"}).status_code == 401
    assert client.post("/api/alerts/dispatch", json={}).status_code == 401


def test_api_clock_and_dispatch_dedupe(client):
    r = client.post("/api/clock/asof", json={"date": "2024-05-21"}, headers=KEY)
    assert r.status_code == 200 and r.json()["asOf"] == "2024-05-21"
    a = client.get("/api/alerts/active").json()
    assert a["count"] > 0
    first = client.post("/api/alerts/dispatch", json={}, headers=KEY).json()["dispatched"]
    again = client.post("/api/alerts/dispatch", json={}, headers=KEY).json()["dispatched"]
    assert len(first) > 0 and len(again) == 0
    assert all(m["message"].startswith("[EXERCISE]") for m in first)
    aid = a["alerts"][0]["alertId"]
    assert client.get(f"/api/alerts/{aid}/cap.xml").status_code == 200


def test_api_counterfactual_bounded(client):
    r = client.post("/api/counterfactual", json={"city": "Ahmedabad", "measures": list(C.INTERVENTIONS), "coolingCentres": 10}).json()
    assert 0 <= r["averted"]["excess"] <= r["baseline"]["excess"]
