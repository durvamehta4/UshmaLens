import { useEffect, useMemo, useState } from "react";
import { Circle, CircleMarker, GeoJSON, MapContainer, TileLayer, Tooltip } from "react-leaflet";
import { useApp, useData } from "../lib/state.jsx";
import { api } from "../lib/api.js";
import { SEQ, fmt, fmtInt, seqColor } from "../lib/bands.js";
import { Loading, Tile } from "../components/ui.jsx";

export default function PlanPage() {
  const { meta, version, city: prefCity } = useApp();
  const [city, setCity] = useState(prefCity === "India" ? "Ahmedabad" : prefCity);
  const [n, setN] = useState(6);
  const [radius, setRadius] = useState(1.5);
  const [leads, setLeads] = useState("1,2,3");
  const [measures, setMeasures] = useState(["work_hours", "advisories"]);
  const [res, setRes] = useState(null);
  const [cf, setCf] = useState(null);
  const [err, setErr] = useState(null);
  const geo = useData(`/cities/${city}/geometry`, [city]);
  const alloc = useData(`/cities/${city}/allocation?leads=${leads}`, [city, leads]);
  const L = leads.split(",").map(Number);

  useEffect(() => {
    let alive = true;
    setErr(null);
    Promise.all([
      api.post("/optimize/cooling", { city, n, radiusKm: radius, leads: L }),
      api.post("/counterfactual", { city, coolingCentres: n, radiusKm: radius, measures, leads: L }),
    ])
      .then(([a, b]) => alive && (setRes(a), setCf(b)))
      .catch((e) => alive && setErr(e.message));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [city, n, radius, measures.join(), leads, version]);

  const byKey = useMemo(() => Object.fromEntries((alloc.data?.wards ?? []).map((w) => [w.key, w])), [alloc.data]);
  const rateMax = useMemo(() => {
    const xs = (alloc.data?.wards ?? []).map((w) => (1e5 * w.excess) / Math.max(w.population, 1)).sort((a, b) => a - b);
    return Math.max(xs[Math.floor(xs.length * 0.95)] ?? 0, 0.05);
  }, [alloc.data]);
  const rate = (k) => (byKey[k] ? (1e5 * byKey[k].excess) / Math.max(byKey[k].population, 1) : null);
  const covered = new Set(res?.centres.flatMap((c) => c.newlyCovered) ?? []);
  const cm = meta?.cities.find((c) => c.city === city);
  const toggle = (m) => setMeasures((xs) => (xs.includes(m) ? xs.filter((x) => x !== m) : [...xs, m]));

  if (!meta) return <div className="page"><Loading /></div>;
  return (
    <div className="page">
      <h1>Plan & simulate</h1>
      <p className="lede">“I can open N cooling centres tomorrow — where?” and “what does a package of Heat Action Plan measures buy us?”
        Sites are chosen to cover the most forecast heat-attributable deaths within walking distance; measures are applied to the same forecast with their uncertainty.</p>
      <div className="card row" style={{ marginBottom: 14, alignItems: "end" }}>
        <label className="field">City
          <select value={city} onChange={(e) => setCity(e.target.value)}>{meta.cities.map((c) => <option key={c.city}>{c.city}</option>)}</select>
        </label>
        <label className="field">Horizon
          <select value={leads} onChange={(e) => setLeads(e.target.value)}>
            <option value="1,2,3">D+1 to D+3</option><option value="1,2,3,4,5">D+1 to D+5</option><option value="0">Issue day only</option>
          </select>
        </label>
        <label className="field">Cooling centres: <b>{n}</b>
          <input type="range" min="0" max="30" value={n} onChange={(e) => setN(Number(e.target.value))} />
        </label>
        <label className="field">Walking radius: <b>{radius} km</b>
          <input type="range" min="0.5" max="4" step="0.25" value={radius} onChange={(e) => setRadius(Number(e.target.value))} />
        </label>
      </div>
      <div className="card" style={{ marginBottom: 14 }}>
        <h2>Citywide measures</h2>
        <div className="row">
          {Object.entries(meta.interventions).filter(([k]) => k !== "cooling_centres").map(([k, v]) => (
            <label key={k} className="row small" style={{ gap: 6 }}>
              <input type="checkbox" checked={measures.includes(k)} onChange={() => toggle(k)} />
              {v.label} <span className="muted">(−{Math.round(v.averted * 100)}%)</span>
            </label>
          ))}
        </div>
      </div>
      {err && <div className="sentence" role="alert">{err}</div>}
      {cf && (
        <div className="tiles">
          <Tile k="Forecast heat deaths" v={fmt(cf.baseline.excess, 1)} s={`range ${fmt(cf.baseline.low, 1)}–${fmt(cf.baseline.high, 1)} · ${city}`} />
          <Tile k="Deaths averted" v={fmt(cf.averted.excess, 1)} s={`range ${fmt(cf.averted.low, 1)}–${fmt(cf.averted.high, 1)}`} />
          <Tile k="Risk inside coverage" v={res ? `${Math.round(res.coveredRiskShare * 100)}%` : "–"} s={res ? `${res.coveredWards} of ${res.totalWards} wards · ${fmtInt(res.coveredPopulation)} people` : ""} />
          <Tile k="Issued" v={cf.asOf} s="forecast the plan is built on" />
        </div>
      )}
      <div className="grid two">
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <div style={{ height: 520 }}>
            {geo.data && cm && (
              <MapContainer key={city} bounds={cm.basemap?.bbox ? [[cm.basemap.bbox[1], cm.basemap.bbox[0]], [cm.basemap.bbox[3], cm.basemap.bbox[2]]] : undefined}
                center={[cm.lat, cm.lon]} zoom={11} style={{ height: "100%" }} preferCanvas>
                <TileLayer url="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}" maxZoom={16} attribution="Tiles &copy; Esri" opacity={0.5} />
                <GeoJSON key={`${city}-${alloc.data?.asOf}-${res?.centres.length}-${radius}-${leads}`} data={geo.data}
                  style={(f) => ({ color: covered.has(f.properties.key) ? "#0b0b0b" : "#ffffff", weight: covered.has(f.properties.key) ? 1.6 : 0.6,
                    fillColor: seqColor(rate(f.properties.key), rateMax), fillOpacity: 0.8 })}
                  onEachFeature={(f, l) => l.bindTooltip(() => `${f.properties.name}: ${fmt(rate(f.properties.key), 2)} excess deaths /100k over horizon${covered.has(f.properties.key) ? " · covered" : ""}`, { className: "ushma-tip", sticky: true })} />
                {res?.centres.map((c) => <Circle key={`r${c.key}`} center={[c.lat, c.lon]} radius={radius * 1000} pathOptions={{ color: "#b8321f", weight: 1, fillOpacity: 0.04, dashArray: "4 4" }} />)}
                {res?.centres.map((c) => (
                  <CircleMarker key={c.key} center={[c.lat, c.lon]} radius={9} pathOptions={{ color: "#ffffff", weight: 2, fillColor: "#b8321f", fillOpacity: 1 }}>
                    <Tooltip permanent direction="center" className="ushma-tip">{c.rank}</Tooltip>
                  </CircleMarker>
                ))}
              </MapContainer>
            )}
          </div>
          <div style={{ padding: "8px 12px" }} className="row small">
            <span>Excess deaths /100k:</span>
            {SEQ.map((c) => <span key={c} style={{ background: c, width: 22, height: 10, display: "inline-block" }} />)}
            <span className="muted">0 – {fmt(rateMax, 2)}+</span><span>· numbered = recommended centres</span>
          </div>
        </div>
        <div className="card">
          <h2>Recommended cooling-centre sites</h2>
          {!res ? <Loading /> : res.centres.length === 0 ? <div className="muted small">Set at least one centre.</div> : (
            <table className="data">
              <thead><tr><th className="num">#</th><th>Site (ward centroid)</th><th className="num">New wards covered</th></tr></thead>
              <tbody>{res.centres.map((c) => <tr key={c.key}><td className="num">{c.rank}</td><td>{c.name}</td><td className="num">{c.newlyCovered.length}</td></tr>)}</tbody>
            </table>
          )}
          {res && <p className="small muted">{res.method}. {res.assumption}</p>}
          {cf && <p className="small muted">{cf.note}</p>}
          <h3>Ward priority for resources</h3>
          {!alloc.data ? <Loading /> : (
            <div className="table-wrap" style={{ maxHeight: 280 }}>
              <table className="data small">
                <thead><tr><th className="num">#</th><th>Ward</th><th className="num">Excess deaths</th><th className="num">Share</th></tr></thead>
                <tbody>{alloc.data.wards.slice(0, 25).map((w) => <tr key={w.key}><td className="num">{w.rank}</td><td>{w.name}</td><td className="num">{fmt(w.excess, 2)}</td><td className="num">{Math.round(w.share * 100)}%</td></tr>)}</tbody>
              </table>
            </div>
          )}
          <a className="btn" style={{ marginTop: 10 }} href={`/api/cities/${city}/export.csv`}>Download ward audit CSV</a>
        </div>
      </div>
    </div>
  );
}
