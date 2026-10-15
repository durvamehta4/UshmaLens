import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from "recharts";
import { useApp, useData } from "../lib/state.jsx";
import { Legend, Loading, Seg, VizTip } from "../components/ui.jsx";
import { fmt } from "../lib/bands.js";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function CityEvidence({ city, b, edges }) {
  const [year, setYear] = useState(String(Number(b.episodes[0]?.peak.slice(0, 4) ?? 2024)));
  const h = useData(`/evidence/history/${city}?start=${year}-03-01&end=${year}-06-30`, [city, year]);
  const rows = useMemo(() => (h.data?.rows ?? []).map((r) => ({ ...r, imd: r.imd_hw ? r.t2m_max : null })), [h.data]);
  const monthly = MONTHS.map((m, i) => ({ m, warning: b.byMonth[i + 1]?.warning ?? 0, imd: b.byMonth[i + 1]?.imd ?? 0 }));
  const years = [];
  for (let y = Number(b.range[0].slice(0, 4)); y <= Number(b.range[1].slice(0, 4)); y++) years.push(String(y));
  return (
    <div className="stack">
      <div className="tiles">
        <div className="card tile"><div className="k">Warning+ days, Mar–Jun</div><div className="v">{fmt(100 * b.shareWarningPlusMarJun, 1)}%</div><div className="s">city HTSI · median ward {fmt(100 * b.wardShareWarningPlusMarJun.median, 1)}%</div></div>
        <div className="card tile"><div className="k">Missed by IMD temperature rule</div><div className="v">{b.warningMissedByImd} / {b.warningDays}</div><div className="s">Warning+ days with no IMD heat wave · mean Tmax {fmt(b.meanTmaxOnMissed)}°, WBGT {fmt(b.meanWbgtOnMissed)}°</div></div>
        <div className="card tile"><div className="k">Humid-heat share</div><div className="v">{fmt(100 * (b.humidShareOfWarning ?? 0), 0)}%</div><div className="s">of Warning+ days driven by WBGT, not air temperature</div></div>
        <div className="card tile"><div className="k">Record</div><div className="v">{b.days.toLocaleString()} d</div><div className="s">ERA5 {b.range[0]} → {b.range[1]}</div></div>
      </div>
      <div className="grid two">
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2>Heat season {year}: HTSI vs air temperature</h2>
            <select value={year} onChange={(e) => setYear(e.target.value)} aria-label="Year">{years.map((y) => <option key={y}>{y}</option>)}</select>
          </div>
          <Legend items={[{ label: "HTSI (city, population-weighted)", color: "var(--s1)" }, { label: "Daily max air temperature", color: "var(--s2)" }, { label: "IMD heat-wave day", color: "var(--band-emergency)" }]} />
          {!h.data ? <Loading /> : (
            <div className="chart tall">
              <ResponsiveContainer>
                <ComposedChart data={rows} margin={{ top: 8, right: 30, bottom: 0, left: -14 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="date" tickFormatter={(d) => d.slice(5)} minTickGap={30} tickLine={false} />
                  <YAxis yAxisId="h" domain={[0, 100]} tickLine={false} axisLine={false} />
                  <YAxis yAxisId="t" orientation="right" domain={[20, 50]} tickLine={false} axisLine={false} />
                  {edges.map((e, i) => <ReferenceLine key={e} yAxisId="h" y={e} stroke="var(--axis)" strokeDasharray="3 3" label={{ value: ["Watch", "Warning", "Emergency"][i], position: "insideLeft", fill: "var(--ink-3)", fontSize: 10 }} />)}
                  <Line yAxisId="h" dataKey="htsi" name="HTSI" stroke="var(--s1)" dot={false} strokeWidth={2} isAnimationActive={false} />
                  <Line yAxisId="t" dataKey="t2m_max" name="Tmax °C" stroke="var(--s2)" dot={false} strokeWidth={1.5} isAnimationActive={false} />
                  <Scatter yAxisId="t" dataKey="imd" name="IMD heat wave" fill="var(--band-emergency)" isAnimationActive={false} />
                  <Tooltip content={<VizTip fmt={(v) => fmt(v)} />} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
        <div className="card">
          <h2>Alert days by month, {b.range[0].slice(0, 4)}–{b.range[1].slice(0, 4)}</h2>
          <Legend items={[{ label: "HTSI Warning+ days", color: "var(--s1)", box: true }, { label: "IMD heat-wave days", color: "var(--s2)", box: true }]} />
          <div className="chart tall">
            <ResponsiveContainer>
              <BarChart data={monthly} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="m" tickLine={false} />
                <YAxis tickLine={false} axisLine={false} allowDecimals={false} />
                <Bar dataKey="warning" name="HTSI Warning+" fill="var(--s1)" isAnimationActive={false} />
                <Bar dataKey="imd" name="IMD heat wave" fill="var(--s2)" isAnimationActive={false} />
                <Tooltip content={<VizTip />} cursor={{ fill: "var(--surface-2)" }} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <h3>Worst episodes (replayable from the clock)</h3>
          <table className="data small">
            <thead><tr><th>Peak</th><th>Window</th><th className="num">Max HTSI</th><th className="num">Warning+ days</th></tr></thead>
            <tbody>{b.episodes.map((e) => <tr key={e.peak}><td>{e.peak}</td><td>{e.start} → {e.end}</td><td className="num">{e.maxHtsi}</td><td className="num">{e.daysWarningPlus}</td></tr>)}</tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default function EvidencePage() {
  const { meta } = useApp();
  const ev = useData("/evidence");
  const [city, setCity] = useState("Ahmedabad");
  if (!ev.data || !meta) return <div className="page"><Loading /></div>;
  const { backtest, calibration, riskCurves, htsiWeights } = ev.data;
  return (
    <div className="page">
      <h1>Evidence</h1>
      <p className="lede">Every number here is regenerated by <code>scripts/build_history.py</code> from 12+ years of hourly ERA5 reanalysis run through exactly the physics the live forecast uses.
        It shows how the Human Thermal Stress Index behaves against the temperature-only rule India's warnings use today, and where each modelling choice comes from.</p>
      <Seg label="City" value={city} onChange={setCity} options={meta.cities.map((c) => [c.city, c.city])} />
      <div style={{ marginTop: 12 }}><CityEvidence key={city} city={city} b={backtest.cities[city]} edges={meta.bandEdges} /></div>

      <div className="grid two" style={{ marginTop: 14 }}>
        <div className="card">
          <h2>How the index is built</h2>
          <table className="data small">
            <thead><tr><th>Channel</th><th className="num">Weight</th><th>Captures</th></tr></thead>
            <tbody>
              <tr><td>Intensity</td><td className="num">{htsiWeights.intensity}</td><td>Daily-max UTCI (human-body radiant temperature, hourly) on its official stress bands</td></tr>
              <tr><td>Anomaly</td><td className="num">{htsiWeights.anomaly}</td><td>Percentile for this place & day of year — the more unusual of humid heat (WBGT) and dry heat (air temperature)</td></tr>
              <tr><td>Night relief</td><td className="num">{htsiWeights.night_relief}</td><td>Overnight WBGT above 26 °C or overnight air above 29 °C, with the ward's own night-time heat island</td></tr>
              <tr><td>Duration</td><td className="num">{htsiWeights.duration}</td><td>Consecutive days above the 90th percentile, saturating</td></tr>
            </tbody>
          </table>
          <p className="small muted">WBGT is Liljegren's heat-balance model (wet-wick + 150 mm globe); UTCI uses the radiant temperature of a standing person, not a black globe. Both run hourly, then aggregate — pairing daily-max temperature with daily-mean humidity describes an hour that never happened.</p>
          <h3>Band edges</h3>
          <p className="small">Watch {calibration.bandEdges[0]} · Warning {calibration.bandEdges[1]} · Emergency {calibration.bandEdges[2]} — {calibration.rule}. (USHMA's Odisha grid edges were {calibration.ushmaOdishaEdges.join(" / ")}; ward-level heat islands lift the distribution, so the same rule gives higher edges here.)</p>
        </div>
        <div className="card">
          <h2>Heat–mortality curves</h2>
          <table className="data small">
            <thead><tr><th>City</th><th className="num">RR at reference (95% CI)</th><th className="num">Reference HTSI</th><th>Source</th></tr></thead>
            <tbody>
              {Object.values(riskCurves).map((c) => (
                <tr key={c.city}><td>{c.city}</td><td className="num">{c.rrAtReference.central} ({c.rrAtReference.low}–{c.rrAtReference.high})</td><td className="num">{c.referenceHtsi}</td><td className="muted">{c.source.slice(0, 120)}…</td></tr>
              ))}
            </tbody>
          </table>
          <p className="small muted">Excess deaths = baseline deaths × (RR − 1), with RR rising log-linearly above the Watch edge over a 0–3 day lag and modified on the log scale by ward vulnerability and cooling access. No term is fitted to deaths: no paired local weather–mortality series exists for these cities. The reference HTSI is each city's 97th-percentile day, matching the anchor study's exposure definition.</p>
          <h3>Known limitations</h3>
          <ul className="small">
            <li>ERA5 under-reads station maxima in cities by 1–3 °C, so the IMD comparison here undercounts IMD heat-wave days.</li>
            <li>Ward population is apportioned from city Census totals by area × built-up share.</li>
            <li>Replay forecasts are perfect-prognosis; live uncertainty comes from the GFS ensemble at the city centre, transferred to wards.</li>
            <li>Hindi, Odia and state-language advisories are drafts pending native-speaker review.</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
