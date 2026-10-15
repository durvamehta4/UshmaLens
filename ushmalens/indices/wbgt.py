"""Wet Bulb Globe Temperature.

Two implementations are provided, and the difference between them is the single
most important scientific point in this project.

``wbgt_shade`` -- the Australian BOM approximation
    ``0.567*T + 0.393*e + 3.94``. Simple, popular, and *wrong for our purpose*:
    it ignores wind and solar radiation entirely, so it cannot distinguish a
    breezy overcast 38 degC from a still, blazing 38 degC. It is included only as
    the comparison baseline, because it is what the supplied notebook used.

``wbgt_liljegren`` -- Liljegren et al. (2008)
    A physical model that solves the heat balance of a wetted wick and of a
    150 mm black globe, given air temperature, humidity, wind, barometric
    pressure, and direct/diffuse solar irradiance. This is the reference method
    used by NIOSH and the US military, and it uses exactly the variables the
    supplied hourly grid provides.

    WBGT = 0.7*Tnwb + 0.2*Tg + 0.1*Ta        (outdoors, in sun)

Reference
---------
Liljegren, J.C., Carhart, R.A., Lawday, P., Tschopp, S., Sharp, R. (2008).
"Modeling the Wet Bulb Globe Temperature Using Standard Meteorological
Measurements." Journal of Occupational and Environmental Hygiene, 5(10), 645-655.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

from ushmalens.indices.psychro import saturation_vapour_pressure, vapour_pressure

__all__ = [
    "wbgt_shade",
    "wbgt_liljegren",
    "globe_temperature",
    "natural_wet_bulb",
    "wbgt_category",
    "WBGT_CATEGORIES",
]

# --- physical constants ----------------------------------------------------
STEFAN_BOLTZMANN = 5.67e-8      # W m-2 K-4
GLOBE_DIAMETER_M = 0.15         # ISO 7243 standard 150 mm black globe.
#: This must stay 150 mm: the ISO 7726 constant used to invert globe
#: temperature to mean radiant temperature is calibrated for this diameter,
#: and a 50 mm globe inflates that correction by a factor of 1.54.
GLOBE_EMISSIVITY = 0.95
GLOBE_ALBEDO = 0.05
WICK_DIAMETER_M = 0.007
WICK_LENGTH_M = 0.0254
WICK_EMISSIVITY = 0.95
SURFACE_ALBEDO = 0.20           # cropland / mixed vegetation.
#: Odisha is 37-88% cropland by the supplied HVI data. At 0.4 the
#: ground-reflected term rivals the direct beam, which is not credible
#: outside snow or desert sand.
SURFACE_EMISSIVITY = 0.999
M_AIR = 28.97
M_H2O = 18.015
R_GAS = 8314.34                 # J kmol-1 K-1
R_AIR = R_GAS / M_AIR
CP_AIR = 1003.5                 # J kg-1 K-1
KELVIN = 273.15
#: Latent heat and diffusivity reference values (Liljegren Table 1).
RATIO_CP_MAIR = CP_AIR / 1005.7

#: ISO 7243 / NIOSH occupational risk bands for outdoor WBGT, degrees Celsius.
WBGT_CATEGORIES: tuple[tuple[float, str], ...] = (
    (-np.inf, "Low"),
    (25.0, "Moderate"),
    (28.0, "High"),
    (30.0, "Very High"),
    (32.0, "Extreme"),
)


# ---------------------------------------------------------------------------
# Baseline approximation (comparison only)
# ---------------------------------------------------------------------------


def wbgt_shade(t_c: ArrayLike, rh_pct: ArrayLike) -> NDArray[np.float64]:
    """Australian BOM shade-WBGT approximation, degrees Celsius.

    Present solely as the baseline to beat. It has no wind or radiation term,
    so it cannot represent the conditions that actually determine heat strain
    outdoors.
    """
    t = np.asarray(t_c, dtype=np.float64)
    rh = np.clip(np.asarray(rh_pct, dtype=np.float64), 0.0, 100.0)
    # vapour pressure in hPa, as the original formulation expects
    e_hpa = (rh / 100.0) * 6.105 * np.exp(17.27 * t / (237.7 + t))
    return 0.567 * t + 0.393 * e_hpa + 3.94


# ---------------------------------------------------------------------------
# Air property helpers
# ---------------------------------------------------------------------------


def _air_density(t_k: NDArray[np.float64], p_kpa: NDArray[np.float64]) -> NDArray[np.float64]:
    return p_kpa * 1000.0 / (R_AIR * t_k)


def _thermal_conductivity(t_k: NDArray[np.float64]) -> NDArray[np.float64]:
    """Thermal conductivity of air, W m-1 K-1.

    An empirical power law anchored on k = 0.02624 W/m/K at 300 K. The
    Eucken-style form ``(Cp + 0.25*R_air) * mu`` that appears in some WBGT
    codes returns ~0.0204 here -- 22% low -- which drives the Prandtl number to
    0.93 against air's textbook 0.70, suppresses the convective coefficient and
    leaves the globe running several degrees too hot. That error propagates
    straight into mean radiant temperature, where it is amplified by the
    fourth-power inversion.
    """
    return 0.02624 * np.power(t_k / 300.0, 0.8646)


def _viscosity(t_k: NDArray[np.float64]) -> NDArray[np.float64]:
    """Dynamic viscosity of air, kg m-1 s-1 (Sutherland's law)."""
    return 1.458e-6 * np.power(t_k, 1.5) / (t_k + 110.4)


def _diffusivity(t_k: NDArray[np.float64], p_kpa: NDArray[np.float64]) -> NDArray[np.float64]:
    """Water-vapour diffusivity in air, m2 s-1."""
    p_atm = p_kpa / 101.325
    return 2.471e-5 * (t_k / 298.0) ** 1.75 / p_atm


def _latent_heat(t_k: NDArray[np.float64]) -> NDArray[np.float64]:
    """Latent heat of vaporisation, J kg-1."""
    return (2.501 - 0.00237 * (t_k - KELVIN)) * 1e6


#: Solar constant, W/m2. Direct normal irradiance at the surface can never
#: exceed this -- the bound that stops a small solar zenith from producing an
#: unbounded beam term.
SOLAR_CONSTANT = 1367.0

#: Below this cosine the sun is effectively on the horizon; beam geometry is
#: numerically meaningless there and the irradiance is negligible anyway.
MIN_COS_ZENITH = 0.0873  # ~85 degrees


def _absorbed_shortwave(
    solar_wm2: NDArray[np.float64],
    cos_zenith: NDArray[np.float64],
    direct_fraction: NDArray[np.float64],
    albedo_body: float,
) -> NDArray[np.float64]:
    """Mean absorbed shortwave flux density over a sphere, W/m2.

    Decomposed explicitly rather than folded into a single fitted factor, so
    each term is checkable:

    * **Direct beam** -- a sphere presents projected area ``pi r^2`` against
      surface area ``4 pi r^2``, so the mean flux is ``B_n / 4`` where ``B_n`` is
      beam irradiance normal to the sun.
    * **Diffuse sky** -- isotropic over the upper hemisphere, mean flux ``D / 2``.
    * **Ground reflection** -- isotropic over the lower hemisphere, mean flux
      ``albedo_surface * GHI / 2``.

    ``B_n`` is reconstructed as ``beam_horizontal / cos(z)`` and then clipped to
    the solar constant. Without that clip a low sun drives the beam term to
    absurd values; that is what produced 87 degC WBGT in the first version of this
    function.
    """
    cz = np.clip(cos_zenith, 0.0, 1.0)
    fdir = np.clip(direct_fraction, 0.0, 1.0)

    # Hard physical ceiling: global horizontal irradiance cannot exceed the
    # extraterrestrial horizontal irradiance, ``S0 * cos(z)``. The 1.1 allows
    # for cloud-edge enhancement, which genuinely can exceed clear-sky briefly.
    # Reanalysis rounding and any upstream regridding can otherwise pair a high
    # GHI with a low sun, and the beam term then drives the globe tens of
    # degrees too hot.
    ghi = np.maximum(solar_wm2, 0.0)
    ghi = np.minimum(ghi, 1.1 * SOLAR_CONSTANT * cz)

    beam_horizontal = fdir * ghi
    cz_eff = np.maximum(cz, MIN_COS_ZENITH)
    beam_normal = np.minimum(beam_horizontal / cz_eff, SOLAR_CONSTANT)
    # Sun below the effective horizon contributes no beam.
    beam_normal = np.where(cz < MIN_COS_ZENITH, 0.0, beam_normal)

    diffuse = np.maximum(ghi - beam_horizontal, 0.0)

    return (1.0 - albedo_body) * (
        beam_normal / 4.0 + diffuse / 2.0 + SURFACE_ALBEDO * ghi / 2.0
    )


# ---------------------------------------------------------------------------
# Globe temperature
# ---------------------------------------------------------------------------


def globe_temperature(
    t_c: ArrayLike,
    dewpoint_c: ArrayLike,
    wind_ms: ArrayLike,
    pressure_kpa: ArrayLike,
    solar_wm2: ArrayLike,
    cos_zenith: ArrayLike,
    direct_fraction: ArrayLike,
    max_iter: int = 50,
    tol: float = 0.02,
) -> NDArray[np.float64]:
    """Solve the black-globe heat balance for globe temperature, degrees Celsius.

    The globe gains heat from direct beam, diffuse sky and ground-reflected
    shortwave plus longwave from sky and ground, and loses it by radiation and
    forced convection. The balance is implicit in Tg, so it is iterated.
    """
    ta = np.asarray(t_c, dtype=np.float64) + KELVIN
    td = np.asarray(dewpoint_c, dtype=np.float64)
    u = np.maximum(np.asarray(wind_ms, dtype=np.float64), 0.13)
    p = np.asarray(pressure_kpa, dtype=np.float64)
    s = np.maximum(np.asarray(solar_wm2, dtype=np.float64), 0.0)
    cz = np.clip(np.asarray(cos_zenith, dtype=np.float64), 0.0, 1.0)
    fdir = np.clip(np.asarray(direct_fraction, dtype=np.float64), 0.0, 1.0)

    # Sky emissivity from vapour pressure (Oke / Brutsaert style).
    e_kpa = vapour_pressure(np.asarray(t_c, dtype=np.float64), td)
    emis_sky = 0.575 * np.power(np.maximum(e_kpa, 1e-4) * 10.0, 1.0 / 7.0)
    emis_sky = np.clip(emis_sky, 0.5, 1.0)

    sw_absorbed = _absorbed_shortwave(s, cz, fdir, GLOBE_ALBEDO)

    tg = ta.copy()
    for _ in range(max_iter):
        tg_k = tg
        rho = _air_density((ta + tg_k) / 2.0, p)
        mu = _viscosity((ta + tg_k) / 2.0)
        k = _thermal_conductivity((ta + tg_k) / 2.0)

        re = np.maximum(rho * u * GLOBE_DIAMETER_M / mu, 1e-3)
        pr = CP_AIR * mu / k

        # Forced convection over a sphere (Whitaker / Ranz-Marshall).
        nu_forced = 2.0 + 0.6 * np.sqrt(re) * np.cbrt(pr)

        # Free convection (Churchill). A forced-only model badly underestimates
        # heat transfer in near-calm air and lets the globe run tens of degrees
        # too hot -- which matters precisely on still nights, when night-time
        # heat stress is the thing we most need to get right.
        nu_kin = mu / rho
        beta = 1.0 / np.maximum((ta + tg_k) / 2.0, 1.0)
        gr = (
            9.81 * beta * np.abs(tg_k - ta) * GLOBE_DIAMETER_M**3
            / np.maximum(nu_kin**2, 1e-12)
        )
        ra = gr * pr
        nu_free = 2.0 + (
            0.589 * np.power(np.maximum(ra, 0.0), 0.25)
            / np.power(1.0 + np.power(0.469 / pr, 9.0 / 16.0), 4.0 / 9.0)
        )

        # Combine the two regimes; the cubic blend is the usual form and reduces
        # to whichever mechanism dominates.
        nu = np.cbrt(nu_forced**3 + nu_free**3)
        h = np.maximum(nu * k / GLOBE_DIAMETER_M, 1e-6)

        # Longwave gain: sky over the upper hemisphere, ground over the lower.
        # Ground surface temperature is approximated by air temperature.
        lw = GLOBE_EMISSIVITY * STEFAN_BOLTZMANN * (
            0.5 * emis_sky * ta**4 + 0.5 * SURFACE_EMISSIVITY * ta**4
        )
        loss = GLOBE_EMISSIVITY * STEFAN_BOLTZMANN * tg_k**4

        new_tg = ta + (sw_absorbed + lw - loss) / h
        # Damped update: the quartic term makes an undamped step oscillate.
        new_tg = tg_k + 0.5 * (new_tg - tg_k)
        if np.nanmax(np.abs(new_tg - tg_k)) < tol:
            tg = new_tg
            break
        tg = new_tg

    return tg - KELVIN


# ---------------------------------------------------------------------------
# Natural wet bulb temperature
# ---------------------------------------------------------------------------


def natural_wet_bulb(
    t_c: ArrayLike,
    dewpoint_c: ArrayLike,
    wind_ms: ArrayLike,
    pressure_kpa: ArrayLike,
    solar_wm2: ArrayLike,
    cos_zenith: ArrayLike,
    direct_fraction: ArrayLike,
    max_iter: int = 50,
    tol: float = 0.02,
) -> NDArray[np.float64]:
    """Solve the wetted-wick heat balance, degrees Celsius.

    Unlike psychrometric wet bulb, the *natural* wet bulb is exposed to sun and
    ambient wind, so it carries a radiative load. This is the 0.7-weighted term
    in outdoor WBGT and the reason WBGT responds to sunshine at all.
    """
    ta_c = np.asarray(t_c, dtype=np.float64)
    ta = ta_c + KELVIN
    td = np.asarray(dewpoint_c, dtype=np.float64)
    u = np.maximum(np.asarray(wind_ms, dtype=np.float64), 0.13)
    p = np.asarray(pressure_kpa, dtype=np.float64)
    s = np.maximum(np.asarray(solar_wm2, dtype=np.float64), 0.0)
    cz = np.clip(np.asarray(cos_zenith, dtype=np.float64), 0.0, 1.0)
    fdir = np.clip(np.asarray(direct_fraction, dtype=np.float64), 0.0, 1.0)

    e_air = vapour_pressure(ta_c, td)
    emis_sky = np.clip(0.575 * np.power(np.maximum(e_air, 1e-4) * 10.0, 1.0 / 7.0), 0.5, 1.0)
    # The wick absorbs more shortwave than the globe reflects (alpha ~ 0.85).
    sw_absorbed = _absorbed_shortwave(s, cz, fdir, albedo_body=0.15)

    tw = ta_c.copy()
    for _ in range(max_iter):
        tw_k = tw + KELVIN
        tfilm = (ta + tw_k) / 2.0
        rho = _air_density(tfilm, p)
        mu = _viscosity(tfilm)
        k = _thermal_conductivity(tfilm)
        dv = _diffusivity(tfilm, p)
        lam = _latent_heat(tw_k)

        re = np.maximum(rho * u * WICK_DIAMETER_M / mu, 1e-3)
        pr = CP_AIR * mu / k
        sc = mu / (rho * dv)

        # Forced convection over a cylinder.
        nu = 0.281 * re**0.6 * pr**0.44
        sh = 0.281 * re**0.6 * sc**0.44
        h = nu * k / WICK_DIAMETER_M
        hmass = sh * dv / WICK_DIAMETER_M

        e_surf = saturation_vapour_pressure(tw)

        # Net radiative load on the wick.
        lw = WICK_EMISSIVITY * STEFAN_BOLTZMANN * (
            0.5 * emis_sky * ta**4 + 0.5 * SURFACE_EMISSIVITY * ta**4 - tw_k**4
        )
        rad = sw_absorbed + lw

        # Evaporative cooling, convective exchange, radiative load.
        mflux = hmass * rho * 0.622 * (e_surf - e_air) / np.maximum(p, 1e-6)
        new_tw = ta_c + (rad - lam * mflux) / np.maximum(h, 1e-6)

        # The natural wet bulb is bounded below by the dewpoint (it cannot
        # evaporate past saturation) and only marginally exceeds air temperature,
        # and then only in near-saturated air under strong sun.
        new_tw = np.clip(new_tw, td - 1.0, ta_c + 4.0)
        new_tw = tw + 0.4 * (new_tw - tw)
        if np.nanmax(np.abs(new_tw - tw)) < tol:
            tw = new_tw
            break
        tw = new_tw

    return tw


def wbgt_liljegren(
    t_c: ArrayLike,
    dewpoint_c: ArrayLike,
    wind_ms: ArrayLike,
    pressure_kpa: ArrayLike,
    solar_wm2: ArrayLike,
    cos_zenith: ArrayLike,
    direct_fraction: ArrayLike,
) -> dict[str, NDArray[np.float64]]:
    """Full outdoor WBGT with its components.

    Returns a dict with ``wbgt``, ``tnwb`` (natural wet bulb) and ``tg`` (globe)
    so the composition is inspectable rather than a single opaque number.
    """
    tg = globe_temperature(
        t_c, dewpoint_c, wind_ms, pressure_kpa, solar_wm2, cos_zenith, direct_fraction
    )
    tnwb = natural_wet_bulb(
        t_c, dewpoint_c, wind_ms, pressure_kpa, solar_wm2, cos_zenith, direct_fraction
    )
    ta = np.asarray(t_c, dtype=np.float64)
    return {"wbgt": 0.7 * tnwb + 0.2 * tg + 0.1 * ta, "tnwb": tnwb, "tg": tg}


def wbgt_category(wbgt_c: ArrayLike) -> NDArray[np.str_]:
    """Map WBGT to ISO 7243 style occupational risk bands."""
    w = np.asarray(wbgt_c, dtype=np.float64)
    out = np.full(w.shape, "Low", dtype=object)
    for lower, label in sorted(WBGT_CATEGORIES, key=lambda x: x[0]):
        out = np.where(w >= lower, label, out)
    return out.astype(str)
