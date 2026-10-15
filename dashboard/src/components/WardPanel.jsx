import { useState } from "react";
import { Link } from "react-router-dom";
import { useApp, useData } from "../lib/state.jsx";
import { wardPath } from "../lib/api.js";
import { BandChip, Loading, Stat } from "./ui.jsx";
import ForecastChart from "./ForecastChart.jsx";
import { fmt, fmtInt, pct } from "../lib/bands.js";

function Channels({ d, weights }) {
  const rows = [
    ["intensity", "Intensity", "peak UTCI on its official stress bands"],
    ["anomaly", "Anomaly", `unusual for this place & date (${d.regime} heat, ${fmt(d.pctRank, 0)}th pct)`],
    ["nightRelief", "Night relief", "humid night (WBGT > 26 °C) or hot night (air > 29 °C)"],
    ["duration", "Duration", `${d.spellDays} consecutive hot day(s)`],
  ];
  const w = { intensity: weights.intensity, anomaly: weights.anomaly, nightRelief: weights.night_relief, duration: weights.duration };
  return (
    <table className="data small">
      <thead><tr><th>HTSI channel</th><th className="num">Score</th><th className="num">× weight</th><th className="num">Points</th></tr></thead>
      <tbody>
        {rows.map(([k, label, why]) => (
          <tr key={k}>
            <td>{label}<div className="muted">{why}</div></td>
            <td className="num">{fmt(d.channels[k], 0)}</td>
            <td className="num">{w[k]}</td>
            <td className="num"><b>{fmt(d.channels[k] * w[k], 1)}</b></td>
          </tr>
        ))}
        <tr><td><b>HTSI</b></td><td /><td /><td className="num"><b>{fmt(d.htsi, 1)}</b></td></tr>
      </tbody>
    </table>
  );
}

function DaysTable({ days }) {
  return (
    <div className="table-wrap" style={{ maxHeight: "none" }}>
      <table className="data">
        <thead>
          <tr><th>Day</th><th>Band</th><th className="num">HTSI (80%)</th><th className="num">P(Warn+)</th><th className="num">Tmax / WBGT</th><th className="num">Excess deaths</th></tr>
        </thead>
        <tbody>
          {days.map((d) => (
            <tr key={d.date}>
              <td>{d.lead === 0 ? "Issue day" : `D+${d.lead}`}<div className="muted small">{d.date.slice(5)}</div></td>
              <td><BandChip band={d.band} /></td>
              <td className="num">{fmt(d.htsi)}<div className="muted small">{d.observed ? "observed" : `${fmt(d.q10, 0)}–${fmt(d.q90, 0)}`}</div></td>
              <td className="num">{d.observed ? "–" : pct(d.pWarning)}</td>
              <td className="num">{fmt(d.tmax, 0)}° / {fmt(d.wbgt, 1)}°<div className="muted small">night {fmt(d.nightWbgt, 1)}°</div></td>
              <td className="num">{fmt(d.excessDeaths, 3)}<div className="muted small">{fmt(d.excessLow, 3)}–{fmt(d.excessHigh, 3)}</div></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function WardPanel({ wardKey, lead, onClose }) {
  const { meta } = useApp();
  const path = wardPath(wardKey);
  const city = wardKey.split(":")[0];
  const cityLang = meta.cities.find((c) => c.city === city)?.lang ?? "hi";
  const ward = useData(`/wards/${path}`, [wardKey]);
  const [lang, setLang] = useState(cityLang);
  const [audience, setAudience] = useState("public");
  const advisory = useData(`/advisory/ward/${encodeURIComponent(wardKey)}?lang=${lang}&audience=${audience}`, [wardKey, lang, audience]);

  if (ward.loading && !ward.data) return <Loading what="Loading ward" />;
  if (ward.error) return <div role="alert">{ward.error}</div>;
  const w = ward.data;
  if (w.key !== wardKey) return <Loading what="Loading ward" />;
  const d = w.days[Math.min(lead, w.days.length - 1)];
  const hap = w.hap;
  const langs = meta.languages.filter((l) => ["en", "hi", cityLang].includes(l.code));

  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h2 style={{ margin: 0 }}>{w.name}</h2>
          <div className="muted small">{w.city}{w.zone ? ` · ${w.zone}` : ""} · {fmt(w.areaKm2)} km² · LCZ {w.lczName ?? "–"}</div>
        </div>
        <button className="btn sm" onClick={onClose} aria-label="Close panel">✕</button>
      </div>

      <div className="row" style={{ marginTop: 10 }}>
        <span className="small muted">Heat Action Plan:</span>
        <BandChip band={hap?.band ?? "Normal"} large />
        {hap?.level > 0 && <span className="small muted">since {hap.since}</span>}
      </div>
      {hap?.target?.reason && <div className="sentence">{hap.target.reason}</div>}

      <div className="statbar">
        <Stat k={lead === 0 ? "HTSI issue day" : `HTSI D+${lead}`} v={fmt(d.htsi, 0)} s={d.observed ? d.band : `80%: ${fmt(d.q10, 0)}–${fmt(d.q90, 0)}`} />
        <Stat k="P(Warning+)" v={d.observed ? "–" : pct(d.pWarning)} s={`P(Emergency) ${d.observed ? "–" : pct(d.pEmergency)}`} />
        <Stat k="Excess deaths" v={fmt(d.excessDeaths, 3)} s={`${fmt(d.excessLow, 3)}–${fmt(d.excessHigh, 3)} · ED +${fmt(d.edSurge, 1)}`} />
      </div>
      <div className="statbar">
        <Stat k="Peak WBGT" v={`${fmt(d.wbgt)}°`} s={`UTCI ${fmt(d.utci, 0)}° · Tmax ${fmt(d.tmax, 0)}°`} />
        <Stat k="Night" v={`${fmt(d.nightWbgt)}°`} s={`WBGT min · air ${fmt(d.nightTmin, 0)}°`} />
        <Stat k="Mortality Risk Index" v={fmt(d.mri, 0)} s={`RR ${fmt(d.rr, 3)}`} />
      </div>

      <h3>Thermal stress, observed and forecast</h3>
      <ForecastChart series={{ history: w.history, forecast: w.days }} edges={meta.bandEdges} />

      <h3>Why this level</h3>
      <div className="sentence small">{w.explain.anomaly}</div>
      <div className="sentence small">{w.explain.uhi}</div>
      <div className="sentence small">{w.explain.risk}</div>
      <Channels d={w.days[Math.min(Math.max(lead, 1), w.days.length - 1)]} weights={meta.htsiWeights} />

      <h3>Next five days</h3>
      <DaysTable days={w.days} />

      <h3>Who lives here, and how exposed</h3>
      <div className="statbar">
        <Stat k="Population (modelled)" v={fmtInt(w.population)} s="Census 2011 × growth, by area × built-up" />
        <Stat k="Built-up" v={`${Math.round(w.builtUp * 100)}%`} s={`UHI +${fmt(w.uhiDayC)}° day / +${fmt(w.uhiNightC)}° night`} />
        <Stat k="Vulnerability" v={`×${fmt(w.vulnMult, 2)}`} s={`Census index ${fmt(w.vIndex, 2)} · cooling ${fmt(w.coolingAccess, 2)}`} />
      </div>
      <table className="data small">
        <tbody>
          <tr><td>MODIS daytime land-surface temperature</td><td className="num">{fmt(w.lstDayC)}° ({w.lstAnom == null ? "–" : `${w.lstAnom > 0 ? "+" : ""}${fmt(w.lstAnom)}° vs city`})</td></tr>
          <tr><td>Vegetation (NDVI) / water fraction</td><td className="num">{fmt(w.ndvi, 2)} / {fmt(w.waterFrac, 2)}</td></tr>
          <tr><td>Households with electricity (HL-14)</td><td className="num">{fmt(w.electricityPct)}%</td></tr>
          <tr><td>Kutcha roofs (HL-14)</td><td className="num">{fmt(w.kutchaPct)}%</td></tr>
          <tr><td>Treated tap water (HL-14)</td><td className="num">{fmt(w.tapWaterPct)}%</td></tr>
          <tr><td>Hospitals per km² (OSM)</td><td className="num">{fmt(w.hospitalsPerKm2, 2)}</td></tr>
        </tbody>
      </table>

      <h3>Advisory</h3>
      <div className="row">
        <div className="seg" role="group" aria-label="Language">
          {langs.map((l) => <button key={l.code} aria-pressed={lang === l.code} onClick={() => setLang(l.code)}>{l.name}</button>)}
        </div>
        <select value={audience} onChange={(e) => setAudience(e.target.value)} aria-label="Audience">
          {meta.audiences.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
        </select>
      </div>
      {advisory.data && (
        <div style={{ marginTop: 8 }}>
          {advisory.data.headline ? <b>{advisory.data.headline}</b> : <span className="muted">No alert in force — routine precautions only.</span>}
          <ul className="actions">{advisory.data.actions.map((a) => <li key={a}>{a}</li>)}</ul>
          {advisory.data.sms && <div className="sentence small"><b>SMS:</b> {advisory.data.sms}</div>}
          {advisory.data.emergency && <div className="sentence small" style={{ borderLeft: "4px solid var(--band-emergency)" }}>{advisory.data.emergency.signs} {advisory.data.emergency.call}</div>}
          {advisory.data.audienceFallback && <div className="muted small">This audience's text is shown in English until a reviewed translation exists.</div>}
          {lang !== "en" && <div className="muted small">{advisory.data.reviewStatus}</div>}
        </div>
      )}
      <div className="row" style={{ marginTop: 10 }}>
        {hap?.alertId && <a className="btn" href={`/api/alerts/${hap.alertId}/cap.xml`} target="_blank" rel="noreferrer">CAP 1.2 XML</a>}
        <Link className="btn" to={`/field?ward=${encodeURIComponent(w.key)}`}>Field view</Link>
      </div>
    </div>
  );
}
