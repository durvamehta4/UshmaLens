// Alert levels are a *state*, so they use the status palette -- and never by
// colour alone: every use carries the icon and the label as well.

export const BANDS = ["Normal", "Watch", "Warning", "Emergency"];

export const BAND_STYLE = {
  Normal: { color: "var(--band-normal)", hex: "#c3c2b7", icon: "●", label: "Normal" },
  Watch: { color: "var(--band-watch)", hex: "#fab219", icon: "▲", label: "Watch" },
  Warning: { color: "var(--band-warning)", hex: "#ec835a", icon: "◆", label: "Warning" },
  Emergency: { color: "var(--band-emergency)", hex: "#d03b3b", icon: "■", label: "Emergency" },
};

// Single-hue sequential ramp (blue) for magnitudes: MRI, excess deaths, vulnerability.
export const SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];

export function seqColor(v, max, min = 0) {
  if (v == null || !Number.isFinite(v) || max <= min) return "#e1e0d9";
  const t = Math.max(0, Math.min(0.999, (v - min) / (max - min)));
  return SEQ[Math.min(SEQ.length - 1, Math.floor(t * SEQ.length))];
}

export const LAYERS = {
  hap: { label: "Heat Action Plan level", kind: "band" },
  htsi: { label: "Thermal stress band (HTSI)", kind: "band" },
  pWarning: { label: "Probability of Warning+", kind: "seq", max: 1 },
  mri: { label: "Mortality Risk Index", kind: "seq", max: 60 },
  excess: { label: "Excess deaths per 100,000 per day", kind: "seq", max: null },
  wbgt: { label: "Peak WBGT (°C)", kind: "seq", min: 26, max: 36 },
  uhiNight: { label: "Night urban heat island (°C)", kind: "seq", max: 3.5 },
  vuln: { label: "Vulnerability", kind: "seq", max: 1 },
};

export const pct = (p) => (p == null ? "–" : `${Math.round(p * 100)}%`);

export const fmt = (v, d = 1) => (v == null || !Number.isFinite(Number(v)) ? "–" : Number(v).toFixed(d));
export const fmtInt = (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-IN"));
export const leadLabel = (k, date) => (k === 0 ? `Today (${date})` : `D+${k} · ${date}`);
