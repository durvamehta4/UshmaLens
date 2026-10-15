import { BAND_STYLE } from "../lib/bands.js";

export function BandChip({ band, large = false, suffix }) {
  const s = BAND_STYLE[band] ?? BAND_STYLE.Normal;
  return (
    <span className={`chip${large ? " lg" : ""}`}>
      <span className="dot" style={{ background: s.color }} aria-hidden="true" />
      <span aria-hidden="true">{s.icon}</span>
      {s.label}
      {suffix}
    </span>
  );
}

export function Stat({ k, v, s }) {
  return (
    <div className="stat">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
      {s && <div className="s">{s}</div>}
    </div>
  );
}

export function Tile({ k, v, s }) {
  return (
    <div className="card tile">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
      {s && <div className="s">{s}</div>}
    </div>
  );
}

export function Seg({ options, value, onChange, label }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map(([v, l]) => (
        <button key={v} aria-pressed={value === v} onClick={() => onChange(v)}>
          {l}
        </button>
      ))}
    </div>
  );
}

/** Chart tooltip in the palette's chrome: text in ink, colour only on the swatch. */
export function VizTip({ active, payload, label, fmt = (v) => v, title }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="viz-tip">
      <div className="t">{title ? title(label) : label}</div>
      {payload
        .filter((p) => p.value != null && !p.dataKey?.startsWith?.("_"))
        .map((p) => (
          <div className="r" key={p.dataKey}>
            <span>
              <span className="sw" style={{ background: p.color ?? p.stroke ?? p.fill }} />
              {p.name}
            </span>
            <b>{fmt(p.value, p.dataKey)}</b>
          </div>
        ))}
    </div>
  );
}

export function Legend({ items }) {
  return (
    <div className="chart-legend">
      {items.map((i) => (
        <span key={i.label}>
          <i className={i.box ? "box" : ""} style={{ background: i.color, opacity: i.opacity ?? 1 }} />
          {i.label}
        </span>
      ))}
    </div>
  );
}

export function Loading({ what = "Loading" }) {
  return <div className="muted small">{what}…</div>;
}
