"""Mortality Risk Index: thermal stress -> expected excess deaths per ward.

A transparent product of named terms (USHMA's model), with each city's curve
now anchored to a *published local* relative risk (TAPAS's calibration anchors):

    excess(w, t)  = base_rate(city) x population(w) x [ RR_ward(w, t) - 1 ]
    log RR_ward   = vuln_mult(w) x min( beta x sum_l lag_w[l] max(HTSI[t-l] - h0, 0), log RR_max )
    beta          = ln(RR_ref) / (HTSI_ref - h0)

* ``h0`` -- the Watch edge: ordinary summer days attribute no deaths.
* ``HTSI_ref`` -- the city's 97th-percentile HTSI day, matching the exposure
  definition of the anchor (de Bont et al. 2024: daily mean temperature above the
  city's 97th percentile on two consecutive days, 2008-2019 death registers).
* ``RR_ref`` -- that study's estimate for the city (Ahmedabad 1.249, Mumbai
  1.034) or its ten-city pooled value (1.147) where a city was not reported;
  the 95% CI gives the low/high curves.
* ``vuln_mult`` -- effect modification on the log scale from ward vulnerability
  and cooling access (places.py), so a more vulnerable ward raises the exponent
  rather than simply multiplying deaths.

No term here is learned from deaths: no paired local weather-mortality series
was available to either project. When ``data/observed/observed.csv`` holds such
a series, ``scripts/fit_local_curve.py`` (roadmap) replaces the anchor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import yaml

from ushmalens import config as C

LAG_WEIGHTS = (0.50, 0.25, 0.15, 0.10)
MAX_RR = 1.8
MRI_REFERENCE_EXCESS = 0.5


@dataclass(frozen=True)
class Curve:
    city: str
    threshold: float
    reference_htsi: float
    rr: tuple[float, float, float]      # low, central, high
    source: str

    def beta(self, which: int = 1) -> float:
        return float(np.log(self.rr[which]) / max(self.reference_htsi - self.threshold, 1e-6))

    def public(self) -> dict:
        return {"city": self.city, "threshold": self.threshold, "referenceHtsi": self.reference_htsi,
                "rrAtReference": {"low": self.rr[0], "central": self.rr[1], "high": self.rr[2]},
                "betaPerHtsiPoint": round(self.beta(), 5), "lagWeights": LAG_WEIGHTS, "maxRR": MAX_RR,
                "source": self.source}


def load_curves(backtest: dict | None) -> dict[str, Curve]:
    anchors = json.loads((C.DATA / "observed" / "published_anchors.json").read_text(encoding="utf-8"))["anchors"]
    er = yaml.safe_load((C.ROOT / "config" / "exposure_response.yaml").read_text(encoding="utf-8"))
    default_rr = er["relative_risk_at_reference"]
    curves = {}
    from ushmalens.cities import CITIES
    for city in CITIES:
        ref = None
        if backtest and city in backtest.get("cities", {}):
            ref = backtest["cities"][city].get("htsiP97")
        ref = max(ref or er["reference_point"]["htsi"], C.BAND_EDGES[0] + 6.0)
        a = next((x for x in anchors if x.get("use") and x["outcome"] == "mort" and city in x["cities"]), None)
        if a:
            lo, hi = a.get("rr_ci") or (a["rr"], a["rr"])
            rr = (lo, a["rr"], hi)
            src = a["source"]
        else:
            rr = (default_rr["low"], default_rr["central"], default_rr["high"])
            src = "USHMA literature-transferred curve (config/exposure_response.yaml)"
        curves[city] = Curve(city, C.BAND_EDGES[0], round(float(ref), 1), tuple(float(x) for x in rr), src)
    return curves


def log_rr(htsi_lags: np.ndarray, curve: Curve, which: int = 1) -> np.ndarray:
    """``htsi_lags``: (n, L) with column l = HTSI at lag l (column 0 = that day)."""
    h = np.nan_to_num(np.asarray(htsi_lags, dtype=float), nan=0.0)
    w = np.asarray(LAG_WEIGHTS)[: h.shape[1]]
    exc = np.maximum(h - curve.threshold, 0.0)
    return np.minimum(curve.beta(which) * (exc * w[None, :]).sum(axis=1), np.log(MAX_RR))


def ward_risk(htsi: np.ndarray, population, vuln_mult, city: str, curve: Curve) -> dict:
    """Risk for runs of consecutive days.

    ``htsi`` is (wards, days) or (days,); ``population`` and ``vuln_mult`` are
    per ward. The first ``len(LAG_WEIGHTS)-1`` days only serve as lags.
    """
    h = np.atleast_2d(np.asarray(htsi, dtype=float))
    n, T = h.shape
    pop = np.broadcast_to(np.asarray(population, dtype=float).reshape(-1, 1), (n, 1))
    vm = np.broadcast_to(np.asarray(vuln_mult, dtype=float).reshape(-1, 1), (n, 1))
    exc = np.maximum(np.nan_to_num(h, nan=0.0) - curve.threshold, 0.0)
    lagged = np.zeros_like(exc)
    for k, wk in enumerate(LAG_WEIGHTS):
        lagged[:, k:] += wk * exc[:, : T - k]
    base = C.CDR_PER_1000[city] / 1000.0 / 365.0 * pop
    out = {"expected": np.broadcast_to(base, (n, T)).copy()}
    for which, name in ((0, "low"), (1, "central"), (2, "high")):
        lrr = np.minimum(curve.beta(which) * lagged, np.log(MAX_RR))
        rr = np.exp(vm * lrr)
        out[f"rr_{name}"] = rr
        out[f"excess_{name}"] = base * (rr - 1.0)
    out["mri"] = np.clip(100 * (out["rr_central"] - 1) / MRI_REFERENCE_EXCESS, 0, 100)
    lo, mid, hi = C.ED_PER_EXCESS_DEATH
    out["ed"] = out["excess_central"] * mid
    out["ed_low"] = out["excess_low"] * lo
    out["ed_high"] = out["excess_high"] * hi
    return out
