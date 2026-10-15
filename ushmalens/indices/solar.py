"""Solar geometry and the beam/diffuse split.

The supplied grid gives ``ALLSKY_SFC_SW_DWN`` -- global horizontal irradiance.
Both the Liljegren WBGT globe solver and any mean-radiant-temperature estimate
need to know how much of that is *direct beam* versus *diffuse sky*, and where
the sun is in the sky. Neither is in the data, so both are derived here.

``pvlib`` is used rather than hand-rolled astronomy: solar position and the
Erbs decomposition are both easy to get subtly wrong, and pvlib's versions are
the validated reference implementations used across the solar industry.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pvlib

__all__ = ["solar_geometry", "SOLAR_COLUMNS"]

SOLAR_COLUMNS = ["cos_zenith", "dni", "dhi", "direct_fraction"]


def solar_geometry(
    ts_utc: pd.Series | pd.DatetimeIndex,
    lat: float,
    lon: float,
    ghi: np.ndarray,
) -> pd.DataFrame:
    """Solar position and beam/diffuse split for one grid cell.

    Parameters
    ----------
    ts_utc
        Timestamps in UTC (naive or tz-aware).
    lat, lon
        Grid cell centre, degrees.
    ghi
        Global horizontal irradiance, W/m2.

    Returns
    -------
    DataFrame with ``cos_zenith``, ``dni``, ``dhi``, ``direct_fraction``.

    Notes
    -----
    ``direct_fraction`` is beam *horizontal* over global horizontal, i.e.
    ``DNI*cos(z) / GHI`` -- this is the quantity Liljegren's radiative load term
    expects, not ``DNI/GHI``.

    Solar position is evaluated at the *midpoint* of each hourly interval. The
    source stamps label interval starts, and using the start systematically
    biases the sun's position half an hour early, which matters near sunrise and
    sunset where the geometry changes fastest.
    """
    idx = pd.DatetimeIndex(pd.Series(ts_utc).to_numpy())
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    mid = idx + pd.Timedelta(minutes=30)

    pos = pvlib.solarposition.get_solarposition(mid, lat, lon)
    zenith = pos["apparent_zenith"].to_numpy()
    cos_z = np.clip(np.cos(np.radians(zenith)), 0.0, 1.0)

    ghi = np.asarray(ghi, dtype=np.float64)
    ghi = np.where(np.isfinite(ghi), np.maximum(ghi, 0.0), 0.0)

    # Erbs correlation: split GHI into direct normal and diffuse horizontal
    # using the clearness index.
    # pvlib returns a DataFrame for Series input and a dict of arrays for
    # ndarray input, so normalise before use.
    erbs = pvlib.irradiance.erbs(ghi, zenith, mid.dayofyear)
    dni = np.nan_to_num(np.asarray(erbs["dni"]), nan=0.0, posinf=0.0)
    dhi = np.nan_to_num(np.asarray(erbs["dhi"]), nan=0.0, posinf=0.0)

    beam_horizontal = dni * cos_z
    with np.errstate(divide="ignore", invalid="ignore"):
        fdir = np.where(ghi > 1.0, beam_horizontal / ghi, 0.0)
    fdir = np.clip(np.nan_to_num(fdir, nan=0.0), 0.0, 1.0)

    return pd.DataFrame(
        {
            "cos_zenith": cos_z.astype(np.float32),
            "dni": dni.astype(np.float32),
            "dhi": dhi.astype(np.float32),
            "direct_fraction": fdir.astype(np.float32),
        }
    )
