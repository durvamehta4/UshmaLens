import { useEffect, useMemo, useState } from "react";
import { CircleMarker, GeoJSON, MapContainer, TileLayer, Tooltip, useMap } from "react-leaflet";
import { useApp, useData } from "../lib/state.jsx";
import { BAND_STYLE, BANDS, LAYERS, SEQ, fmt, fmtInt, pct, seqColor } from "../lib/bands.js";
import { BandChip, Loading, Seg } from "../components/ui.jsx";
import WardPanel from "../components/WardPanel.jsx";

const TILES = {
  light: { url: "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", opacity: 0.6 },
  satellite: { url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", opacity: 0.85 },
};

function FitTo({ bbox, center }) {
  const map = useMap();
  useEffect(() => {
    if (bbox) map.fitBounds([[bbox[1], bbox[0]], [bbox[3], bbox[2]]], { padding: [10, 10] });
    else if (center) map.setView(center, 5);
  }, [bbox, center, map]);
  return null;
}

function valueFor(layer, v) {
  if (!v) return null;
  if (layer === "excess") return v.excessPer100k;
  if (layer === "vuln") return v.vuln;
  return v[layer];
}

function MapLegend({ layer, max, min }) {
  if (LAYERS[layer].kind === "band") {
    return (
      <>
        <b className="small">{LAYERS[layer].label}</b>
        {BANDS.map((b) => (
          <div className="legend-row" key={b}>
            <span className="legend-swatch" style={{ background: BAND_STYLE[b].hex }} />
            <span aria-hidden="true">{BAND_STYLE[b].icon}</span> {b}
          </div>
        ))}
      </>
    );
  }
  return (
    <>
      <b className="small">{LAYERS[layer].label}</b>
      <div className="row" style={{ gap: 0, marginTop: 6 }}>
        {SEQ.map((c) => <span key={c} style={{ background: c, width: 28, height: 12 }} />)}
      </div>
      <div className="row small muted" style={{ justifyContent: "space-between" }}>
        <span>{min ?? 0}</span><span>{layer === "pWarning" ? "100%" : fmt(max, layer === "excess" ? 2 : 1)}+</span>
      </div>
    </>
  );
}

function CityCard({ c, onOpen }) {
  const peak = c.days.slice(0, 6).reduce((a, d) => (d.htsi > a.htsi ? d : a), c.days[0]);
  return (
    <div className="card click-card" onClick={onOpen} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onOpen()}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>{c.city}</h2>
        <BandChip band={c.hap} />
      </div>
      <div className="muted small">{c.state} · {c.wards} wards · {fmtInt(c.population)} people</div>
      {c.hapReason && c.hap !== "Normal" && <div className="sentence small">{c.hapReason}</div>}
      <div className="days-strip">
        {c.days.map((d) => (
          <div key={d.date} className="day" title={`${d.date}: HTSI ${fmt(d.htsi)} (90th pct ward), P(Warning+) ${pct(d.pWarning)}`}>
            <div className="small muted">{d.lead === 0 ? "Today" : `D+${d.lead}`}</div>
            <div className="sw" style={{ background: BAND_STYLE[d.band].hex }} />
            <div className="small"><b>{fmt(d.htsi, 0)}</b></div>
            <div className="small muted">{fmt(d.tmax, 0)}°</div>
          </div>
        ))}
      </div>
      <div className="row small" style={{ marginTop: 6 }}>
        <span>Wards in force:</span>
        {BANDS.slice(1).map((b) => <BandChip key={b} band={b} suffix={<span className="muted"> {c.wardsByHap[b]}</span>} />)}
      </div>
      <div className="small muted" style={{ marginTop: 4 }}>Forecast heat deaths D+1–3: <b>{fmt(c.excess3d, 1)}</b> · peak {peak.date} HTSI {fmt(peak.htsi, 0)}</div>
    </div>
  );
}

function IndiaView() {
  const { setCity } = useApp();
  const ov = useData("/overview");
  const india = useData("/india");
  if (!ov.data) return <div className="page"><Loading what="Loading national view" /></div>;
  return (
    <div className="india">
      <div className="map" style={{ minHeight: 480 }}>
        <MapContainer center={[20.5, 78.5]} zoom={5} minZoom={4} maxZoom={9} preferCanvas>
          <TileLayer url={TILES.light.url} opacity={0.5} attribution="Tiles &copy; Esri" />
          {india.data && <GeoJSON data={india.data} style={{ color: "#898781", weight: 0.6, fillOpacity: 0.03 }} />}
          {ov.data.cities.map((c) => (
            <CircleMarker key={`${c.city}-${c.hap}-${ov.data.asOf}`} center={[c.lat, c.lon]} radius={14}
              pathOptions={{ color: "#ffffff", weight: 2.5, fillColor: BAND_STYLE[c.hap].hex, fillOpacity: 1 }}
              eventHandlers={{ click: () => setCity(c.city) }}>
              <Tooltip direction="right" permanent className="ushma-tip">{c.city} · {c.hap}</Tooltip>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>
      <div className="side">
        <h2 style={{ marginTop: 0 }}>National heat watch · {ov.data.mode === "live" ? "today" : "issued"} {ov.data.asOf}</h2>
        <p className="small muted">City level = worst sustained Heat Action Plan state of the city's 90th-percentile ward. Click a city to open its wards.</p>
        <div className="stack">
          {ov.data.cities.map((c) => <CityCard key={c.city} c={c} onOpen={() => setCity(c.city)} />)}
        </div>
      </div>
    </div>
  );
}

function CityOverview({ snap, wardsMeta, onPick }) {
  const active = useData(`/alerts/active?scope=ward&city=${snap.city}`, [snap.city]);
  const counts = useMemo(() => {
    const c = { Normal: 0, Watch: 0, Warning: 0, Emergency: 0 };
    for (const w of Object.values(snap.wards)) c[w.hap] += 1;
    return c;
  }, [snap]);
  const d = snap.cityDays[snap.lead];
  const ch = snap.cityHap;
  return (
    <div>
      <h2 style={{ marginTop: 0 }}>{snap.city} · {snap.lead === 0 ? `issued ${snap.date}` : `D+${snap.lead}, ${snap.date}`}</h2>
      <div className="row"><span className="small muted">City Heat Action Plan:</span><BandChip band={ch?.band ?? "Normal"} large />{ch?.level > 0 && <span className="small muted">since {ch.since}</span>}</div>
      {ch?.target?.reason && <div className="sentence">{ch.target.reason}</div>}
      <div className="statbar">
        <div className="stat"><div className="k">Heat deaths (excess)</div><div className="v">{fmt(d.excessDeaths, 1)}</div><div className="s">{fmt(d.excessLow, 1)}–{fmt(d.excessHigh, 1)} citywide</div></div>
        <div className="stat"><div className="k">ED surge</div><div className="v">+{fmtInt(d.edSurge)}</div><div className="s">heat-illness visits</div></div>
        <div className="stat"><div className="k">P(Warning+)</div><div className="v">{pct(d.pWarning)}</div><div className="s">90th-pct ward</div></div>
      </div>
      <div className="row small">
        <span>Wards by HAP level:</span>
        {BANDS.map((b) => <BandChip key={b} band={b} suffix={<span className="muted"> {counts[b]}</span>} />)}
      </div>
      <h3>Wards under a Watch or higher</h3>
      {!active.data ? <Loading /> : active.data.alerts.length === 0 ? (
        <div className="muted small">No ward is under a Watch or higher as of {active.data.asOf}.</div>
      ) : (
        <div className="table-wrap" style={{ maxHeight: 420 }}>
          <table className="data">
            <thead><tr><th>Ward</th><th>Level</th><th>Triggered by</th><th className="num">Deaths D+1–3</th></tr></thead>
            <tbody>
              {active.data.alerts.slice(0, 60).map((a) => (
                <tr key={a.key} className="click" onClick={() => onPick(a.id)}>
                  <td>{a.name}<div className="muted small">pop {fmtInt(wardsMeta[a.id]?.population)}</div></td>
                  <td><BandChip band={a.band} /></td>
                  <td className="small">{a.target?.trigger?.lead ? `D+${a.target.trigger.lead} forecast` : "today"}
                    <div className="muted">HTSI {fmt(a.target?.trigger?.htsi, 0)}{a.target?.trigger?.pWarning != null && a.target.trigger.lead ? ` · P ${pct(a.target.trigger.pWarning)}` : ""}</div></td>
                  <td className="num">{fmt(a.forecast.slice(0, 3).reduce((s, f) => s + (f.excessDeaths ?? 0), 0), 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CityView({ city }) {
  const { meta } = useApp();
  const [lead, setLead] = useState(0);
  const [layer, setLayer] = useState("hap");
  const [tiles, setTiles] = useState("light");
  const [sel, setSel] = useState(null);
  const geo = useData(`/cities/${city}/geometry`, [city]);
  const wl = useData(`/cities/${city}/wards`, [city]);
  const snapQ = useData(`/cities/${city}/snapshot?lead=${lead}`, [city, lead]);
  const snap = snapQ.data?.city === city ? snapQ.data : null;
  const cm = meta.cities.find((c) => c.city === city);
  const wardsMeta = useMemo(() => Object.fromEntries((wl.data ?? []).map((w) => [w.key, w])), [wl.data]);
  useEffect(() => setSel(null), [city]);

  const L = LAYERS[layer];
  const max = useMemo(() => {
    if (L.max != null) return L.max;
    const xs = Object.values(snap?.wards ?? {}).map((v) => valueFor(layer, v) ?? 0).sort((a, b) => a - b);
    return Math.max(xs[Math.floor(xs.length * 0.95)] ?? 0, 0.05);
  }, [snap, layer, L.max]);

  const style = (f) => {
    const v = snap?.wards?.[f.properties.key];
    const fill = L.kind === "band" ? BAND_STYLE[(layer === "hap" ? v?.hap : v?.band) ?? "Normal"].hex : seqColor(valueFor(layer, v), max, L.min ?? 0);
    const on = sel === f.properties.key;
    return { color: on ? "#0b0b0b" : "#ffffff", weight: on ? 2.5 : 0.7, fillColor: fill, fillOpacity: tiles === "satellite" ? 0.6 : 0.82 };
  };
  const key = `${city}-${layer}-${lead}-${snap?.asOf}-${sel}-${tiles}`;

  return (
    <div className="mapwrap">
      <div className="map">
        <MapContainer center={[cm.lat, cm.lon]} zoom={11} minZoom={9} maxZoom={16} preferCanvas>
          <TileLayer key={tiles} url={TILES[tiles].url} maxZoom={17} opacity={TILES[tiles].opacity} attribution="Tiles &copy; Esri" />
          <FitTo bbox={cm.basemap?.bbox} />
          {geo.data && snap && (
            <GeoJSON key={key} data={geo.data} style={style}
              onEachFeature={(f, l) => {
                const v = snap.wards[f.properties.key];
                l.on("click", () => setSel(f.properties.key));
                if (v) l.bindTooltip(`<b>${f.properties.name}</b><br/>HAP ${v.hap} · ${v.band} HTSI ${fmt(v.htsi)} (${fmt(v.q10, 0)}–${fmt(v.q90, 0)})<br/>P(Warning+) ${pct(v.pWarning)} · WBGT ${fmt(v.wbgt)}°<br/>excess ${fmt(v.excessDeaths, 3)} deaths/day`, { className: "ushma-tip", sticky: true });
              }} />
          )}
        </MapContainer>
        <div className="overlay tl">
          <select aria-label="Map layer" value={layer} onChange={(e) => setLayer(e.target.value)}>
            {Object.entries(LAYERS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
          <div className="leadslider">
            <label htmlFor="lead" className="small">Lead</label>
            <input id="lead" type="range" min="0" max="5" value={lead} onChange={(e) => setLead(Number(e.target.value))} />
            <b className="small" style={{ minWidth: 100 }}>{lead === 0 ? "Issue day" : `D+${lead}`} {snap?.date ? `· ${snap.date.slice(5)}` : ""}</b>
          </div>
          <Seg label="Basemap" value={tiles} onChange={setTiles} options={[["light", "Map"], ["satellite", "Satellite"]]} />
        </div>
        <div className="overlay bl"><MapLegend layer={layer} max={max} min={L.min} /></div>
      </div>
      <aside className="side" aria-label="Details">
        {sel ? <WardPanel wardKey={sel} lead={lead} onClose={() => setSel(null)} />
          : snap ? <CityOverview snap={snap} wardsMeta={wardsMeta} onPick={setSel} /> : <Loading />}
      </aside>
    </div>
  );
}

export default function MapPage() {
  const { meta, city, setCity } = useApp();
  if (!meta) return <div className="page"><Loading what="Connecting to UshmaLens" /></div>;
  return (
    <div className="mappage">
      <div className="citybar">
        <Seg label="Place" value={city} onChange={setCity}
          options={[["India", "All cities"], ...meta.cities.map((c) => [c.city, c.city])]} />
        {city !== "India" && (() => {
          const c = meta.cities.find((x) => x.city === city);
          return <span className="small muted">{c.state} · {c.wards} wards · district HVI {c.districtHvi?.hvi_tier ?? "–"} · outdoor workers {fmt(c.districtHvi?.outdoor_workers_pct, 0)}%</span>;
        })()}
      </div>
      {city === "India" ? <IndiaView /> : <CityView city={city} />}
    </div>
  );
}
