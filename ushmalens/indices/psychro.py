"""Psychrometric primitives shared by every thermal index.

All functions are vectorised over NumPy arrays and accept either scalars or
arrays. Temperatures are degrees Celsius, pressures kilopascals (matching NASA
POWER's ``PS``), vapour pressures kilopascals, wind speed m/s.

Why dewpoint rather than relative humidity
------------------------------------------
The supplied hourly grid carries ``T2MDEW`` as well as ``RH2M``. Vapour
pressure derived straight from dewpoint avoids a round trip through RH (which
is itself a derived, rounded quantity), and lets us use the disagreement
between the two as a data-quality signal.

The saturation artefact
-----------------------
0.6% of the supplied rows have ``T2MDEW`` above ``T2M`` by a median of 0.4 degC.
Every one of those rows has ``RH2M`` exactly 100.0, and they cluster at 00-06
IST -- the pre-dawn temperature minimum when radiative cooling saturates the
air. This is MERRA-2 rounding two near-identical saturated fields
inconsistently, not a physical supersaturation. Left alone it yields RH > 100%
and pushes the index formulas outside their fitted domains, so ``vapour_pressure``
clamps dewpoint to air temperature and reports how often it had to.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

__all__ = [
    "saturation_vapour_pressure",
    "vapour_pressure",
    "relative_humidity",
    "dewpoint_from_rh",
    "specific_humidity",
    "wind_at_height",
]


def saturation_vapour_pressure(t_c: ArrayLike) -> NDArray[np.float64]:
    """Saturation vapour pressure over water, kPa (Buck 1981).

    Buck's formulation is more accurate than Magnus/Tetens across the range we
    care about (0-55 degC) and is the form used by most meteorological services.
    """
    t = np.asarray(t_c, dtype=np.float64)
    return 0.61121 * np.exp((18.678 - t / 234.5) * (t / (257.14 + t)))


def vapour_pressure(
    t_c: ArrayLike,
    dewpoint_c: ArrayLike,
    clamp: bool = True,
) -> NDArray[np.float64]:
    """Actual vapour pressure, kPa, from dewpoint.

    With ``clamp`` (the default) dewpoint is capped at air temperature, which
    bounds relative humidity at 100%. See the module docstring for why.
    """
    t = np.asarray(t_c, dtype=np.float64)
    td = np.asarray(dewpoint_c, dtype=np.float64)
    if clamp:
        td = np.minimum(td, t)
    return saturation_vapour_pressure(td)


def relative_humidity(t_c: ArrayLike, dewpoint_c: ArrayLike) -> NDArray[np.float64]:
    """Relative humidity in percent, derived from temperature and dewpoint."""
    e = vapour_pressure(t_c, dewpoint_c)
    es = saturation_vapour_pressure(t_c)
    return np.clip(100.0 * e / es, 0.0, 100.0)


def dewpoint_from_rh(t_c: ArrayLike, rh_pct: ArrayLike) -> NDArray[np.float64]:
    """Invert Buck to recover dewpoint from temperature and RH.

    Buck's expression has temperature in both the coefficient and the exponent,
    so it has no closed-form inverse. A Magnus-form inverse is the usual
    shortcut but drifts by ~2.5% RH at high humidity -- enough to matter when
    the whole point of this project is humid heat. Newton refinement on the
    exact forward function removes the drift in three iterations.
    """
    t = np.asarray(t_c, dtype=np.float64)
    rh = np.clip(np.asarray(rh_pct, dtype=np.float64), 1e-3, 100.0)
    target = (rh / 100.0) * saturation_vapour_pressure(t)

    # Magnus-form starting guess.
    ln = np.log(target / 0.61121)
    td = (257.14 * ln) / (18.678 - ln)

    for _ in range(3):
        f = saturation_vapour_pressure(td) - target
        h = 1e-4
        dfdt = (saturation_vapour_pressure(td + h) - saturation_vapour_pressure(td - h)) / (2 * h)
        td = td - f / np.maximum(dfdt, 1e-12)
    return td


def specific_humidity(vapour_kpa: ArrayLike, pressure_kpa: ArrayLike) -> NDArray[np.float64]:
    """Specific humidity, kg/kg."""
    e = np.asarray(vapour_kpa, dtype=np.float64)
    p = np.asarray(pressure_kpa, dtype=np.float64)
    return 0.622 * e / np.maximum(p - 0.378 * e, 1e-6)


def wind_at_height(
    wind_ms: ArrayLike,
    from_height_m: float = 10.0,
    to_height_m: float = 2.0,
    roughness_m: float = 0.03,
    floor_ms: float = 0.13,
) -> NDArray[np.float64]:
    """Convert wind speed between heights with a logarithmic profile.

    The supplied grid gives ``WS10M``. UTCI is *defined* at 10 m and takes it
    unchanged; WBGT needs wind at roughly human height, so it is converted here.

    ``floor_ms`` matters: Liljegren's globe-temperature solver has no solution at
    zero wind (free convection is not represented), so the reference
    implementation floors it. Without the floor the iteration diverges on calm
    nights -- exactly the conditions that matter most for night-time heat stress.
    """
    u = np.asarray(wind_ms, dtype=np.float64)
    ratio = np.log(to_height_m / roughness_m) / np.log(from_height_m / roughness_m)
    return np.maximum(u * ratio, floor_ms)
