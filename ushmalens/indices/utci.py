"""Universal Thermal Climate Index.

UTCI is the index the problem statement names first, and the one the supplied
notebook declared infeasible -- on the grounds that mean radiant temperature was
unavailable. With hourly irradiance, surface pressure and dewpoint in the grid,
Tmrt *is* derivable, so UTCI is computed here.

Implementation
--------------
The UTCI operational procedure is a 6th-order polynomial regression in
(Ta, Tmrt - Ta, wind, vapour pressure) with roughly 210 coefficients. Rather
than transcribe those by hand -- where a single wrong digit produces plausible
but incorrect values -- this module delegates to ``pythermalcomfort``, a
maintained implementation validated against the official reference tables.
``tests/test_indices_reference.py`` pins it against published check values so a
library change cannot silently move our numbers.

Mean radiant temperature
------------------------
Two are computed, because they answer different questions.

``mean_radiant_temperature`` inverts the ISO 7726 relation on the black globe
solved in :mod:`ushma.indices.wbgt`. That is the right quantity for WBGT, which
is *defined* by a globe.

``mean_radiant_temperature_human`` assembles the radiation budget of a standing
person instead -- shortwave absorptivity 0.70 rather than a globe's 0.95, and a
projected area factor that falls to ~0.13 under a high sun where a sphere stays
at 0.25. **This is the one UTCI takes.** Passing the globe value instead inflates
peak Tmrt from roughly 66 degC to 95 degC and drives UTCI off the top of its scale.

Validity domain
---------------
UTCI is defined for Tmrt within 30 degC below to 70 degC above air temperature and
10 m wind between 0.5 and 17 m/s. Inputs are clipped to that box and the number
of clipped rows is reported, because silently extrapolating a regression outside
its fitted range is how indices produce 50 degC WBGT values.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike, NDArray

from ushmalens.indices.wbgt import (
    GLOBE_DIAMETER_M,
    GLOBE_EMISSIVITY,
    KELVIN,
    STEFAN_BOLTZMANN,
)

__all__ = [
    "mean_radiant_temperature",
    "mean_radiant_temperature_human",
    "projected_area_factor",
    "utci",
    "utci_category",
    "UTCI_CATEGORIES",
]

#: Official UTCI thermal-stress bands, degrees Celsius.
UTCI_CATEGORIES: tuple[tuple[float, str], ...] = (
    (-np.inf, "Extreme cold stress"),
    (-40.0, "Very strong cold stress"),
    (-27.0, "Strong cold stress"),
    (-13.0, "Moderate cold stress"),
    (0.0, "Slight cold stress"),
    (9.0, "No thermal stress"),
    (26.0, "Moderate heat stress"),
    (32.0, "Strong heat stress"),
    (38.0, "Very strong heat stress"),
    (46.0, "Extreme heat stress"),
)

#: UTCI validity domain.
TMRT_DELTA_MIN, TMRT_DELTA_MAX = -30.0, 70.0
WIND_MIN, WIND_MAX = 0.5, 17.0


def mean_radiant_temperature(
    globe_c: ArrayLike,
    t_c: ArrayLike,
    wind_ms: ArrayLike,
    globe_diameter_m: float = GLOBE_DIAMETER_M,
    emissivity: float = GLOBE_EMISSIVITY,
) -> NDArray[np.float64]:
    """Mean radiant temperature from globe temperature (ISO 7726, forced convection).

    Tmrt = [ (Tg+273)^4 + (1.10e8 * v^0.6)/(eps * D^0.4) * (Tg - Ta) ]^0.25 - 273
    """
    tg = np.asarray(globe_c, dtype=np.float64)
    ta = np.asarray(t_c, dtype=np.float64)
    v = np.maximum(np.asarray(wind_ms, dtype=np.float64), 0.0)

    term = (1.10e8 * np.power(v, 0.6)) / (emissivity * np.power(globe_diameter_m, 0.4))
    inner = np.power(tg + KELVIN, 4.0) + term * (tg - ta)
    inner = np.maximum(inner, 1.0)
    return np.power(inner, 0.25) - KELVIN


def utci(
    t_c: ArrayLike,
    tmrt_c: ArrayLike,
    wind_ms: ArrayLike,
    rh_pct: ArrayLike,
    clip: bool = True,
) -> NDArray[np.float64]:
    """UTCI equivalent temperature, degrees Celsius.

    ``wind_ms`` must be the 10 m wind, which is the height UTCI is defined at --
    do not convert it to 2 m first.
    """
    from pythermalcomfort.models import utci as _utci

    ta = np.asarray(t_c, dtype=np.float64)
    tr = np.asarray(tmrt_c, dtype=np.float64)
    v = np.asarray(wind_ms, dtype=np.float64)
    rh = np.clip(np.asarray(rh_pct, dtype=np.float64), 0.0, 100.0)

    if clip:
        tr = ta + np.clip(tr - ta, TMRT_DELTA_MIN, TMRT_DELTA_MAX)
        v = np.clip(v, WIND_MIN, WIND_MAX)

    res = _utci(tdb=ta, tr=tr, v=v, rh=rh, units="SI", limit_inputs=False)
    out = np.asarray(getattr(res, "utci", res), dtype=np.float64)
    return out


def utci_category(utci_c: ArrayLike) -> NDArray[np.str_]:
    """Map UTCI to its official thermal-stress bands."""
    u = np.asarray(utci_c, dtype=np.float64)
    out = np.full(u.shape, "Extreme cold stress", dtype=object)
    for lower, label in sorted(UTCI_CATEGORIES, key=lambda x: x[0]):
        out = np.where(u >= lower, label, out)
    return out.astype(str)


def clipping_report(
    t_c: ArrayLike, tmrt_c: ArrayLike, wind_ms: ArrayLike
) -> pd.Series:
    """How many inputs fall outside UTCI's fitted domain."""
    ta = np.asarray(t_c, dtype=np.float64)
    tr = np.asarray(tmrt_c, dtype=np.float64)
    v = np.asarray(wind_ms, dtype=np.float64)
    d = tr - ta
    n = ta.size
    return pd.Series(
        {
            "n": n,
            "tmrt_delta_below": int((d < TMRT_DELTA_MIN).sum()),
            "tmrt_delta_above": int((d > TMRT_DELTA_MAX).sum()),
            "wind_below": int((v < WIND_MIN).sum()),
            "wind_above": int((v > WIND_MAX).sum()),
        }
    )


# ---------------------------------------------------------------------------
# Mean radiant temperature for a human body
# ---------------------------------------------------------------------------

#: Shortwave absorptivity of a clothed human (VDI 3787 / Hoppe). A black globe
#: is 0.95; using the globe value for a person overstates the radiant load
#: substantially.
HUMAN_SW_ABSORPTIVITY = 0.70

#: Longwave emissivity of the human body.
HUMAN_LW_EMISSIVITY = 0.97


def projected_area_factor(cos_zenith: ArrayLike) -> NDArray[np.float64]:
    """Projected area factor of a standing person, VDI 3787.

    A sphere presents the same projected area from every direction (factor
    0.25). A standing person presents far less to a high sun and more to a low
    one, which is why midday radiant load on a person is much lower than on a
    globe.
    """
    cz = np.clip(np.asarray(cos_zenith, dtype=np.float64), 0.0, 1.0)
    gamma = np.degrees(np.arcsin(cz))  # solar elevation
    return 0.308 * np.cos(np.radians(gamma * (0.998 - gamma**2 / 50000.0)))


def mean_radiant_temperature_human(
    t_c: ArrayLike,
    dewpoint_c: ArrayLike,
    solar_wm2: ArrayLike,
    cos_zenith: ArrayLike,
    direct_fraction: ArrayLike,
    surface_albedo: float = 0.20,
) -> NDArray[np.float64]:
    """Mean radiant temperature experienced by a standing person, degrees Celsius.

    This -- not the black-globe value -- is what UTCI expects. The radiation
    budget is assembled explicitly:

    * direct beam weighted by the standing-person projected area factor,
    * diffuse sky over the upper hemisphere,
    * ground-reflected shortwave over the lower hemisphere,
    * longwave from sky and ground.

    Using a black-globe Tmrt here instead inflates peak values from roughly
    70 degC to 95 degC and pushes UTCI off the top of its scale.
    """
    from ushmalens.indices.psychro import vapour_pressure
    from ushmalens.indices.wbgt import MIN_COS_ZENITH, SOLAR_CONSTANT

    ta = np.asarray(t_c, dtype=np.float64)
    cz = np.clip(np.asarray(cos_zenith, dtype=np.float64), 0.0, 1.0)
    fdir = np.clip(np.asarray(direct_fraction, dtype=np.float64), 0.0, 1.0)

    ghi = np.maximum(np.asarray(solar_wm2, dtype=np.float64), 0.0)
    ghi = np.minimum(ghi, 1.1 * SOLAR_CONSTANT * cz)

    beam_h = fdir * ghi
    beam_n = np.where(
        cz < MIN_COS_ZENITH,
        0.0,
        np.minimum(beam_h / np.maximum(cz, MIN_COS_ZENITH), SOLAR_CONSTANT),
    )
    diffuse = np.maximum(ghi - beam_h, 0.0)
    fp = projected_area_factor(cz)

    sw_abs = HUMAN_SW_ABSORPTIVITY * (
        fp * beam_n + 0.5 * diffuse + 0.5 * surface_albedo * ghi
    )

    # Longwave from sky (vapour-pressure emissivity) and ground (at air temp).
    e_kpa = vapour_pressure(ta, np.asarray(dewpoint_c, dtype=np.float64))
    emis_sky = np.clip(0.575 * np.power(np.maximum(e_kpa, 1e-4) * 10.0, 1.0 / 7.0), 0.5, 1.0)
    ta_k4 = np.power(ta + KELVIN, 4.0)
    lw = 0.5 * emis_sky * ta_k4 + 0.5 * 0.999 * ta_k4

    total = sw_abs / (HUMAN_LW_EMISSIVITY * STEFAN_BOLTZMANN) + lw
    return np.power(np.maximum(total, 1.0), 0.25) - KELVIN
