import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Legend, VizTip } from "./ui.jsx";
import { fmt } from "../lib/bands.js";

/** Observed history, then the D+1..D+5 forecast with its calibrated 80% band. */
export default function ForecastChart({ series, edges }) {
  if (!series) return null;
  const rows = [
    ...series.history.map((h) => ({ date: h.date, observed: h.htsi })),
    ...series.forecast.map((f, i) => ({
      date: f.date,
      observed: f.observed ? f.htsi : undefined,
      median: f.htsi,
      band: f.observed ? undefined : [f.q10, f.q90],
      q10: f.q10,
      q90: f.q90,
    })),
  ];
  const short = (d) => d?.slice(5);
  return (
    <div>
      <Legend
        items={[
          { label: "Observed", color: "var(--s1)" },
          { label: "Forecast median", color: "var(--s1)", opacity: 0.55 },
          { label: "80% range (ensemble / lead-time error)", color: "var(--s1)", opacity: 0.18, box: true },
        ]}
      />
      <div className="chart">
        <ResponsiveContainer>
          <ComposedChart data={rows} margin={{ top: 8, right: 64, bottom: 0, left: -18 }}>
            <CartesianGrid vertical={false} />
            <XAxis dataKey="date" tickFormatter={short} minTickGap={24} tickLine={false} />
            <YAxis domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tickLine={false} axisLine={false} />
            {edges.map((e, i) => (
              // Warning and Emergency edges are close together, so their
              // labels sit on opposite sides of the line to avoid colliding.
              <ReferenceLine key={e} y={e} stroke="var(--axis)" strokeDasharray="3 3"
                label={{ value: ["Watch", "Warning", "Emergency"][i], position: "right", fill: "var(--ink-3)", fontSize: 11, dy: i === 1 ? 7 : i === 2 ? -7 : 0 }} />
            ))}
            <ReferenceLine x={series.forecast[0]?.date} stroke="var(--ink-3)" label={{ value: "as of", position: "top", fill: "var(--ink-3)", fontSize: 11 }} />
            <Area dataKey="band" name="80% band" stroke="none" fill="var(--s1)" fillOpacity={0.18} isAnimationActive={false} activeDot={false} />
            <Line dataKey="observed" name="Observed HTSI" stroke="var(--s1)" strokeWidth={2} dot={false} activeDot={{ r: 5, strokeWidth: 2, stroke: "var(--surface)" }} isAnimationActive={false} connectNulls={false} />
            <Line dataKey="median" name="Forecast HTSI" stroke="var(--s1)" strokeOpacity={0.55} strokeDasharray="5 4" strokeWidth={2} dot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)", fill: "var(--s1)" }} isAnimationActive={false} />
            <Tooltip
              content={<VizTip fmt={(v) => (Array.isArray(v) ? `${fmt(v[0])}–${fmt(v[1])}` : fmt(v))} />}
              cursor={{ stroke: "var(--axis)" }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
