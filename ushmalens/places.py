"""Cities and wards: geometry, people, built form and who is most at risk.

Ward polygons and every per-ward attribute are TAPAS's real data: satellite
built/vegetation/water fractions from imagery, WUDAPT Local Climate Zones,
MODIS land-surface temperature and NDVI, Census HL-14 amenities and OSM
hospitals. On top of them this module derives, with each step labelled:

* **UHI uplift** (USHMA's parameterisation) driven by the ward's *measured*
  built-up share and nudged by its MODIS LST anomaly -- replacing USHMA's
  synthetic built-up values with observations.
* **Population** apportioned from the city's Census total by area x built-up
  share (a modelled split; ward Census totals were not published for these
  boundaries).
* **Vulnerability multiplier** for the risk model, combining TAPAS's seven-
  indicator Census index (V) and per-ward cooling access (AC) with the district
  heat-vulnerability row (outdoor workers, poverty, travel time to care).
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field

import pandas as pd

from ushmalens import config as C
from ushmalens.cities import CITIES

MUMBAI_AREAS = {
    "A": "Colaba / Fort", "B": "Mandvi / Dongri", "C": "Marine Lines", "D": "Mahalaxmi / Opera House",
    "E": "Byculla / Mazgaon", "F/N": "Parel", "F/S": "Lalbaug / Matunga", "G/N": "Mahim / Dharavi",
    "G/S": "Lower Parel / Mahalaxmi", "H/E": "Bandra E / Santacruz", "H/W": "Bandra W / Khar",
    "K/E": "Andheri E / Jogeshwari", "K/W": "Andheri W / Versova", "L": "Kurla",
    "M/E": "Chembur / Govandi", "M/W": "Chembur / Mankhurd", "N": "Ghatkopar / Vikhroli",
    "P/N": "Malad / Marve", "P/S": "Goregaon / Aarey", "R/C": "Borivali", "R/N": "Borivali / Dahisar",
    "R/S": "Kandivali", "S": "Bhandup", "T": "Mulund",
}

V_WEIGHTS = {"children": 0.22, "literacy": 0.13, "slum": 0.18, "elderly": 0.13,
             "disability": 0.09, "density": 0.13, "kutcha": 0.12}
AC_MOD = {"built": 0.18, "green": 0.16, "elec": 0.16, "health": 0.10, "water": 0.08}


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


@dataclass
class Ward:
    city: str
    id: str
    name: str
    zone: str | None
    lon: float
    lat: float
    area_km2: float
    geometry: dict
    sat: dict = field(default_factory=dict)
    built: float = 0.5
    lst_anom: float | None = None
    d_day: float = 0.0
    d_night: float = 0.0
    population: float = 0.0
    v_index: float = 0.5
    v_parts: dict = field(default_factory=dict)
    ac: float = 0.5
    ac_detail: dict = field(default_factory=dict)
    vuln_score: float = 0.5
    vuln_mult: float = 1.0
    cell: str = ""

    @property
    def key(self):
        return f"{self.city}:{self.id}"

    def public(self) -> dict:
        s = self.sat
        return {
            "key": self.key, "city": self.city, "id": self.id, "name": self.name, "zone": self.zone,
            "lat": round(self.lat, 5), "lon": round(self.lon, 5), "areaKm2": round(self.area_km2, 2),
            "population": round(self.population), "builtUp": round(self.built, 3),
            "vegFrac": s.get("veg_frac"), "waterFrac": s.get("wat_frac"),
            "lcz": s.get("lcz"), "lczName": s.get("lcz_name"),
            "lstDayC": s.get("lst_day_c"), "lstAnom": None if self.lst_anom is None else round(self.lst_anom, 2),
            "ndvi": s.get("ndvi_modis"), "electricityPct": s.get("elec_pct"), "kutchaPct": s.get("kutcha_pct"),
            "tapWaterPct": s.get("tap_pct"), "hospitalsPerKm2": s.get("health_per_km2"),
            "uhiDayC": round(self.d_day, 2), "uhiNightC": round(self.d_night, 2),
            "vIndex": round(self.v_index, 3), "vParts": self.v_parts,
            "coolingAccess": round(self.ac, 3), "coolingDetail": self.ac_detail,
            "vulnScore": round(self.vuln_score, 3), "vulnMult": round(self.vuln_mult, 3),
            "cell": self.cell,
        }


def _district_hvi() -> dict:
    """City -> district HVI social indicators, with a national percentile for each."""
    path = C.DATA / "national" / "india_district_hvi_final.csv"
    if not path.exists():
        return {}
    d = pd.read_csv(path)
    cols = {"outdoor_workers_pct": True, "mpi_poverty_pct": True, "healthcare_travel_min": True}
    for c in cols:
        d[c + "_pctile"] = d[c].rank(pct=True)
    out = {}
    for city, cfg in CITIES.items():
        rows = d[d["district"].isin(cfg["hvi_districts"])]
        if rows.empty:
            continue
        w = rows["pop_total"].clip(lower=1)
        rec = {c: float((rows[c] * w).sum() / w.sum()) for c in list(cols) + [c + "_pctile" for c in cols]}
        rec["hvi_score"] = float((rows["hvi_score"] * w).sum() / w.sum())
        rec["hvi_tier"] = rows.iloc[0]["hvi_tier"]
        rec["districts"] = cfg["hvi_districts"]
        # Social sensitivity from the district row: equal-weighted national percentiles.
        rec["social"] = (rec["outdoor_workers_pct_pctile"] + rec["mpi_poverty_pct_pctile"]
                         + rec["healthcare_travel_min_pctile"]) / 3.0
        out[city] = rec
    return out


class Places:
    def __init__(self):
        self.wards: dict[str, Ward] = {}
        self.by_city: dict[str, list[Ward]] = {c: [] for c in CITIES}
        self.city_meta: dict[str, dict] = {}
        self.hvi = _district_hvi()
        for city in CITIES:
            self._load_city(city)
        self._vulnerability()

    # ------------------------------------------------------------------ loading
    def _load_city(self, city):
        cdir = C.CITY_DIR / city
        fc = _load(cdir / "wards.geojson")
        mm = _load(cdir / "map.json") or {}
        hl = _load(C.DATA / "hl14" / f"{city.lower()}_parsed.json") or {}
        health = (_load(cdir / "health.json") or {}).get("wards", {})
        lcz = (_load(cdir / "lcz.json") or {}).get("wards", {})
        modis = _load(cdir / "modis.json") or {}
        lst = (modis.get("lst") or {}).get("wards", {})
        ndvi = (modis.get("ndvi") or {}).get("wards", {})
        hlw, hlt = hl.get("wards") or {}, hl.get("city_total") or {}
        for f in fc["features"]:
            p = f["properties"]
            wid = str(p["id"])
            sat = dict(p.get("satellite") or {})
            r = hlw.get(wid)
            src = r or hlt
            sat.update({"elec_pct": src.get("elec_pct"), "kutcha_pct": src.get("roof_kutcha_pct"),
                        "tap_pct": src.get("tap_treated_pct"), "hl14_level": "ward" if r else "city"})
            if wid in health:
                sat["health_per_km2"] = health[wid].get("per_km2")
            if wid in lcz:
                sat.update({"lcz": lcz[wid].get("lcz"), "lcz_name": lcz[wid].get("lcz_name"),
                            "lcz_built_share": lcz[wid].get("built_share")})
            if wid in lst:
                sat["lst_day_c"] = lst[wid]
            if wid in ndvi:
                sat["ndvi_modis"] = ndvi[wid]
            label = (p.get("label") or "").strip() or f"Ward {wid}"
            if city == "Mumbai":
                code = label.replace("Ward ", "").strip()
                if code in MUMBAI_AREAS:
                    label = f"{label} · {MUMBAI_AREAS[code]}"
            lon, lat = p["centroid"]
            w = Ward(city=city, id=wid, name=label, zone=p.get("zone"), lon=lon, lat=lat,
                     area_km2=float(p.get("area_km2") or 1.0), geometry=f["geometry"], sat=sat)
            self.wards[w.key] = w
            self.by_city[city].append(w)
        self.city_meta[city] = {
            "basemap": {"image": f"/api/cities/{city}/basemap.jpg", "bbox": mm.get("bbox"),
                        "source": mm.get("tile_source")},
            "boundarySource": mm.get("boundary_source"),
            "modisDate": (modis.get("lst") or {}).get("date"),
        }
        self._derive_city(city)

    def _derive_city(self, city):
        wards = self.by_city[city]
        cfg = CITIES[city]

        def med(key):
            vals = [w.sat[key] for w in wards if w.sat.get(key) is not None]
            return statistics.median(vals) if vals else None

        m = {k: med(k) for k in ("built_frac", "veg_frac", "lst_day_c", "ndvi_modis",
                                 "health_per_km2", "elec_pct", "tap_pct")}
        # Built-up share: satellite fraction blended with the LCZ built share.
        for w in wards:
            s = w.sat
            b = s.get("built_frac")
            lb = s.get("lcz_built_share")
            if b is None and lb is None:
                built = 0.5
            elif b is None:
                built = lb
            elif lb is None:
                built = b
            else:
                built = 0.6 * b + 0.4 * lb
            w.built = clamp(built)
            if s.get("lst_day_c") is not None and m["lst_day_c"] is not None:
                w.lst_anom = s["lst_day_c"] - m["lst_day_c"]
            u = C.UHI
            lst_adj = 0.0 if w.lst_anom is None else max(-u["lst_cap"], min(u["lst_cap"], u["lst_gain"] * w.lst_anom))
            w.d_day = u["a_day"] + u["b_day"] * w.built + lst_adj
            w.d_night = u["a_night"] + u["b_night"] * w.built + 0.5 * lst_adj

        # Population: city Census total x growth, split by area x built-up share.
        weights = [w.area_km2 * (0.15 + w.built) for w in wards]
        tot = sum(weights) or 1.0
        pop = cfg["population_2011"] * C.POP_GROWTH_SINCE_2011
        for w, wt in zip(wards, weights):
            w.population = pop * wt / tot

        # Cooling access (TAPAS): city base x modifiers from real ward inputs.
        for w in wards:
            s = w.sat
            use_ndvi = s.get("ndvi_modis") is not None and m["ndvi_modis"] is not None
            green, gmed = (s.get("ndvi_modis"), m["ndvi_modis"]) if use_ndvi else (s.get("veg_frac"), m["veg_frac"])
            dbn = clamp((w.built - (m["built_frac"] or 0.5)) / 0.30, -1, 1)
            dgn = clamp((green - gmed) / 0.15, -1, 1) if green is not None and gmed is not None else 0.0
            eln = clamp((s["elec_pct"] - m["elec_pct"]) / 4.0, -1, 1) if s.get("elec_pct") is not None and m["elec_pct"] is not None else 0.0
            hln = clamp((s["health_per_km2"] - m["health_per_km2"]) / 2.0, -1, 1) if s.get("health_per_km2") is not None and m["health_per_km2"] is not None else 0.0
            wn = clamp((s["tap_pct"] - m["tap_pct"]) / 12.0, -1, 1) if s.get("tap_pct") is not None and m["tap_pct"] is not None else 0.0
            mod = 1 - AC_MOD["built"] * dbn + AC_MOD["green"] * dgn + AC_MOD["elec"] * eln + AC_MOD["health"] * hln + AC_MOD["water"] * wn
            w.ac = clamp(cfg["ac_base"] * mod, 0.05, 0.90)
            w.ac_detail = {"base": cfg["ac_base"], "modifier": round(mod, 3), "greenSource": "MODIS NDVI" if use_ndvi else "imagery vegetation"}

        # Census vulnerability index (TAPAS), kutcha refined per ward where HL-14 matched.
        v = cfg["vuln"]
        base = {}
        if v.get("children_0_6") is not None: base["children"] = clamp(v["children_0_6"] / 0.13)
        if v.get("literacy") is not None: base["literacy"] = clamp((1 - v["literacy"]) / 0.20)
        if v.get("slum") is not None: base["slum"] = clamp(v["slum"] / 0.60)
        if v.get("elderly_60plus") is not None: base["elderly"] = clamp(v["elderly_60plus"] / 0.15)
        if v.get("disability") is not None: base["disability"] = clamp(v["disability"] / 0.03)
        if v.get("density") is not None: base["density"] = clamp(v["density"] / 25000)
        for w in wards:
            parts = dict(base)
            k = w.sat.get("kutcha_pct")
            parts["kutcha"] = clamp((k / 100 if k is not None else v.get("kutcha", 0.02)) / 0.20)
            sub = sum(V_WEIGHTS[p] for p in parts)
            w.v_parts = {p: round(x, 3) for p, x in parts.items()}
            w.v_index = sum(V_WEIGHTS[p] / sub * parts[p] for p in parts)

    def _vulnerability(self):
        """Pool all wards, then map a combined score onto the effect-modifier range."""
        raw = []
        for w in self.wards.values():
            social = self.hvi.get(w.city, {}).get("social", 0.5)
            w.vuln_score = 0.45 * w.v_index + 0.35 * (1 - w.ac) + 0.20 * social
            raw.append(w.vuln_score)
        lo, hi = min(raw), max(raw)
        a, b = C.VULN_MULT_RANGE
        for w in self.wards.values():
            t = (w.vuln_score - lo) / (hi - lo) if hi > lo else 0.5
            w.vuln_mult = a + (b - a) * t

    # ------------------------------------------------------------------ weather cells
    def assign_cells(self, step_deg: float = 0.05) -> dict[str, dict]:
        """Snap ward centroids to a weather grid; return {cell_id: {lat, lon, city}}."""
        cells = {}
        for w in self.wards.values():
            la = round(round(w.lat / step_deg) * step_deg, 3)
            lo = round(round(w.lon / step_deg) * step_deg, 3)
            cid = f"{la:.3f},{lo:.3f}"
            w.cell = cid
            cells.setdefault(cid, {"lat": la, "lon": lo, "city": w.city})
        return cells

    # ------------------------------------------------------------------ queries
    def city_wards(self, city) -> list[Ward]:
        if city not in self.by_city:
            raise KeyError(city)
        return self.by_city[city]

    def get(self, city, wid) -> Ward:
        return self.wards[f"{city}:{wid}"]

    def geojson(self, city) -> dict:
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": w.key, "properties": {"key": w.key, "id": w.id, "name": w.name, "city": city},
             "geometry": w.geometry} for w in self.city_wards(city)]}


def haversine_km(a_lat, a_lon, b_lat, b_lon) -> float:
    r = math.radians
    d_lat, d_lon = r(b_lat - a_lat), r(b_lon - a_lon)
    h = math.sin(d_lat / 2) ** 2 + math.cos(r(a_lat)) * math.cos(r(b_lat)) * math.sin(d_lon / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))
