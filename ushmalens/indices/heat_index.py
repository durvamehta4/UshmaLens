"""NWS Heat Index (Rothfusz regression).

This is the "feels like" number US weather services publish. It is a regression
fitted to Steadman's (1979) apparent-temperature tables, valid in shade with
light wind, and it accounts for temperature and humidity only -- no wind, no
radiation. That limitation is exactly why the problem statement asks for WBGT
and UTCI as well; Heat Index is retained because it is the number the public
and the press already recognise.

Ported from the working implementation in ``thermal_stress_index.ipynb`` and
extended with the NOAA switching rule so the low-temperature branch is handled
correctly.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

__all__ = ["heat_index", "heat_index_category", "HI_CATEGORIES"]

#: NWS Heat Index caution bands, in degrees Celsius.
#: (lower bound inclusive, label, short advice key)
HI_CATEGORIES: tuple[tuple[float, str], ...] = (
    (54.0, "Extreme Danger"),
    (41.0, "Danger"),
    (32.0, "Extreme Caution"),
    (27.0, "Caution"),
    (-np.inf, "Safe"),
)


def heat_index(t_c: ArrayLike, rh_pct: ArrayLike) -> NDArray[np.float64]:
    """Heat Index in degrees Celsius from air temperature (degC) and RH (%).

    Implements the full NOAA procedure:

    1. Compute the simple (Steadman) form.
    2. If the average of that and the air temperature is below 80 degF, return it --
       the Rothfusz regression is not valid there and produces nonsense.
    3. Otherwise apply the Rothfusz regression, plus the low-humidity and
       high-humidity adjustments.
    """
    t_f = np.asarray(t_c, dtype=np.float64) * 9.0 / 5.0 + 32.0
    rh = np.clip(np.asarray(rh_pct, dtype=np.float64), 0.0, 100.0)

    # Step 1: simple form.
    hi_simple = 0.5 * (t_f + 61.0 + (t_f - 68.0) * 1.2 + rh * 0.094)

    # Step 2: NOAA's switching rule.
    use_full = ((hi_simple + t_f) / 2.0) >= 80.0

    # Step 3: Rothfusz regression.
    hi_full = (
        -42.379
        + 2.04901523 * t_f
        + 10.14333127 * rh
        - 0.22475541 * t_f * rh
        - 0.00683783 * t_f**2
        - 0.05481717 * rh**2
        + 0.00122874 * t_f**2 * rh
        + 0.00085282 * t_f * rh**2
        - 0.00000199 * t_f**2 * rh**2
    )

    # Dry-air adjustment: the regression over-predicts at very low humidity.
    sqrt_term = np.clip((17.0 - np.abs(t_f - 95.0)) / 17.0, 0.0, None)
    adj_low = ((13.0 - rh) / 4.0) * np.sqrt(sqrt_term)
    cond_low = (rh < 13.0) & (t_f >= 80.0) & (t_f <= 112.0)
    hi_full = np.where(cond_low, hi_full - adj_low, hi_full)

    # Humid adjustment: it under-predicts in a narrow warm, very humid band.
    adj_high = ((rh - 85.0) / 10.0) * ((87.0 - t_f) / 5.0)
    cond_high = (rh > 85.0) & (t_f >= 80.0) & (t_f <= 87.0)
    hi_full = np.where(cond_high, hi_full + adj_high, hi_full)

    hi_f = np.where(use_full, hi_full, hi_simple)
    return (hi_f - 32.0) * 5.0 / 9.0


def heat_index_category(hi_c: ArrayLike) -> NDArray[np.str_]:
    """Map Heat Index values to NWS caution bands."""
    hi = np.asarray(hi_c, dtype=np.float64)
    out = np.full(hi.shape, "Safe", dtype=object)
    # Ascending order so that each higher band overwrites the one below it.
    for lower, label in sorted(HI_CATEGORIES, key=lambda x: x[0]):
        out = np.where(hi >= lower, label, out)
    return out.astype(str)
