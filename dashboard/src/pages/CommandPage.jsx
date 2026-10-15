import { useCallback, useEffect, useState } from "react";
import { useApp, useData } from "../lib/state.jsx";
import { api } from "../lib/api.js";
import { BandChip, Loading, Seg } from "../components/ui.jsx";
import { fmt, pct } from "../lib/bands.js";

function Checklist({ alert }) {
  const { version, bump } = useApp();
  const [data, setData] = useState(null);
  const [msg, setMsg] = useState(null);
  useEffect(() => {
    api.get(`/alerts/${alert.alertId}/checklist`).then(setData).catch((e) => setMsg(e.message));
  }, [alert.alertId, version]);
  const toggle = async (item, done) => {
    try {
      setData(await api.post(`/alerts/${alert.alertId}/checklist`, { item, done, by: "dashboard" }));
      bump();
    } catch (e) {
      setMsg(e.message);
    }
  };
  if (!data) return <Loading />;
  let last = null;
  return (
    <div>
      {data.items.map((i) => {
        const head = i.audience !== last ? <div className="small muted" style={{ marginTop: 6 }}>{i.audience}</div> : null;
        last = i.audience;
        return (
          <div key={i.item}>
            {head}
            <label className="row small" style={{ margin: "3px 0" }}>
              <input type="checkbox" checked={i.done} onChange={(e) => toggle(i.item, e.target.checked)} />
              <span style={i.done ? { textDecoration: "line-through", color: "var(--ink-3)" } : null}>{i.item}</span>
              {i.done && <span className="muted">· {i.by}</span>}
            </label>
          </div>
        );
      })}
      {msg && <div className="small" role="alert">{msg}</div>}
    </div>
  );
}

function Subscriptions() {
  const { meta, version } = useApp();
  const [subs, setSubs] = useState(null);
  const [err, setErr] = useState(null);
  const [form, setForm] = useState({ name: "", channel: "sms", address: "", language: "hi", audience: "public", city: "Ahmedabad", minLevel: 2 });
  const load = useCallback(() => api.getAuth("/subscriptions").then(setSubs).catch((e) => setErr(e.message)), []);
  useEffect(() => {
    load();
  }, [load, version]);
  const add = async (e) => {
    e.preventDefault();
    try {
      await api.post("/subscriptions", { name: form.name, channel: form.channel, address: form.address, language: form.language,
        audience: form.audience, minLevel: Number(form.minLevel), cities: [form.city] });
      setForm({ ...form, name: "", address: "" });
      setErr(null);
      load();
    } catch (x) {
      setErr(x.message);
    }
  };
  const remove = async (id) => {
    await api.del(`/subscriptions/${id}`).catch((x) => setErr(x.message));
    load();
  };
  return (
    <div className="card">
      <h2>Subscribers</h2>
      <p className="small muted">Officials, facilities and volunteers get the advisory for their area, audience and language — one message per alert. Channel mode: <b>{meta?.channelMode}</b>{meta?.channelMode === "simulated" ? " (nothing leaves this machine)" : ""}.</p>
      <form className="grid three" onSubmit={add} style={{ alignItems: "end" }}>
        <label className="field">Name<input type="text" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label className="field">Channel
          <select value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>{meta?.channels.map((c) => <option key={c}>{c}</option>)}</select>
        </label>
        <label className="field">{form.channel === "webhook" ? "URL" : "Phone / address"}<input type="text" required value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} placeholder={form.channel === "webhook" ? "https://…" : "+91…"} /></label>
        <label className="field">City
          <select value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })}>{meta?.cities.map((c) => <option key={c.city}>{c.city}</option>)}</select>
        </label>
        <label className="field">Language
          <select value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value })}>{meta?.languages.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}</select>
        </label>
        <label className="field">Audience
          <select value={form.audience} onChange={(e) => setForm({ ...form, audience: e.target.value })}>{meta?.audiences.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}</select>
        </label>
        <label className="field">From level
          <select value={form.minLevel} onChange={(e) => setForm({ ...form, minLevel: e.target.value })}><option value={1}>Watch</option><option value={2}>Warning</option><option value={3}>Emergency</option></select>
        </label>
        <button className="btn primary" type="submit">Add subscriber</button>
      </form>
      {err && <div className="small" role="alert" style={{ marginTop: 8 }}>{err}</div>}
      {subs?.length > 0 && (
        <div className="table-wrap" style={{ maxHeight: 300, marginTop: 12 }}>
          <table className="data">
            <thead><tr><th>Name</th><th>Channel</th><th>Address</th><th>Area</th><th>Lang · audience · from</th><th /></tr></thead>
            <tbody>
              {subs.map((s) => (
                <tr key={s.id}>
                  <td>{s.name}</td><td>{s.channel}</td><td>{s.address}</td>
                  <td>{[...(s.cities ?? []), ...(s.wards ?? [])].join(", ")}</td>
                  <td>{s.language} · {s.audience} · {["", "Watch", "Warning", "Emergency"][s.minLevel]}</td>
                  <td><button className="btn sm" onClick={() => remove(s.id)}>Remove</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Deliveries() {
  const q = useData("/deliveries?limit=60", [], true);
  if (!q.data) return <Loading />;
  if (!q.data.length) return <div className="muted small">Nothing sent yet. Press “Evaluate & dispatch”.</div>;
  return (
    <div className="table-wrap" style={{ maxHeight: 340 }}>
      <table className="data">
        <thead><tr><th>When</th><th>To</th><th>Place</th><th>Level</th><th>Status</th><th>Message</th></tr></thead>
        <tbody>
          {q.data.map((d, i) => (
            <tr key={i}>
              <td className="small">{new Date(d.at).toLocaleTimeString()}</td>
              <td className="small">{d.subscriber}<div className="muted">{d.channel} {d.to}</div></td>
              <td className="small">{d.place}</td>
              <td><BandChip band={d.level} /></td>
              <td className="small">{d.status}</td>
              <td className="small" style={{ maxWidth: 420, whiteSpace: "pre-wrap" }}>{d.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function CommandPage() {
  const { meta, events, bump, city: prefCity } = useApp();
  const [scope, setScope] = useState("city");
  const [city, setCity] = useState(prefCity === "India" ? "" : prefCity);
  const [sel, setSel] = useState(null);
  const [msg, setMsg] = useState(null);
  const active = useData(`/alerts/active?scope=${scope}${city ? `&city=${city}` : ""}`, [scope, city]);
  const history = useData(`/alerts/history?limit=80${city ? `&city=${city}` : ""}`, [city]);
  const audit = useData("/audit?limit=40");

  const dispatch = async (dryRun) => {
    try {
      const r = await api.post("/alerts/dispatch", { dryRun });
      setMsg(`${r.dispatched.length} message(s) ${dryRun ? "would be sent" : "dispatched"} (as of ${r.asOf}). Already-delivered alerts are not re-sent.`);
      bump();
    } catch (e) {
      setMsg(e.message);
    }
  };
  const selected = active.data?.alerts.find((a) => a.key === sel) ?? active.data?.alerts[0];

  return (
    <div className="page">
      <h1>Command</h1>
      <p className="lede">Heat Action Plan status for every city and ward, why each level is in force, the actions it triggers and the messages it sends. Levels escalate on the ensemble forecast and step down only after the heat has clearly passed.</p>
      <div className="row" style={{ marginBottom: 12 }}>
        <Seg label="Scope" value={scope} onChange={setScope} options={[["city", "Cities"], ["ward", "Wards"]]} />
        <select value={city} onChange={(e) => setCity(e.target.value)} aria-label="City filter">
          <option value="">All cities</option>
          {meta?.cities.map((c) => <option key={c.city}>{c.city}</option>)}
        </select>
        {active.data && Object.entries(active.data.counts).map(([b, n]) => <BandChip key={b} band={b} suffix={<span className="muted"> {n}</span>} />)}
        <div className="spacer" />
        <button className="btn" onClick={() => dispatch(true)}>Dry run</button>
        <button className="btn primary" onClick={() => dispatch(false)}>Evaluate &amp; dispatch</button>
      </div>
      {msg && <div className="sentence" role="status">{msg}</div>}

      <div className="grid two">
        <div className="card">
          <h2>Active alerts {active.data && <span className="muted small">issued {active.data.asOf}</span>}</h2>
          {!active.data ? <Loading /> : active.data.alerts.length === 0 ? <div className="muted">No active alerts.</div> : (
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Place</th><th>Level</th><th>Since</th><th className="num">HTSI</th><th className="num">Peak D+1–3</th><th className="num">P(Warn) D+1–2</th></tr></thead>
                <tbody>
                  {active.data.alerts.map((a) => (
                    <tr key={a.key} className={`click${selected?.key === a.key ? " sel" : ""}`} onClick={() => setSel(a.key)}>
                      <td>{a.name}<div className="muted small">{a.scope === "ward" ? a.city : "city"}</div></td>
                      <td><BandChip band={a.band} /></td>
                      <td className="small">{a.since}</td>
                      <td className="num">{fmt(a.today?.htsi, 0)}</td>
                      <td className="num">{fmt(Math.max(...a.forecast.slice(0, 3).map((f) => f.htsi ?? 0)), 0)}</td>
                      <td className="num">{pct(Math.max(...a.forecast.slice(0, 2).map((f) => f.pWarning ?? 0)))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <div className="card">
          <h2>{selected ? selected.name : "Select an alert"}</h2>
          {selected && (
            <div className="stack">
              <div className="row"><BandChip band={selected.band} large /><span className="small muted">since {selected.since} · {selected.alertId}</span></div>
              <div className="sentence">{selected.target?.reason}</div>
              {selected.lastTransition && <div className="small muted">Last change {selected.lastTransition.date}: {selected.lastTransition.from} → {selected.lastTransition.to} ({selected.lastTransition.kind})</div>}
              <h3>Heat Action Plan checklist</h3>
              <Checklist alert={selected} key={selected.alertId} />
              <div className="row">
                <a className="btn" href={`/api/alerts/${selected.alertId}/cap.xml`} target="_blank" rel="noreferrer">CAP 1.2 XML</a>
                <a className="btn" href={`/api/advisory/${selected.scope}/${encodeURIComponent(selected.id)}?audience=municipal`} target="_blank" rel="noreferrer">Municipal advisory (JSON)</a>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="grid two" style={{ marginTop: 14 }}>
        <div className="card">
          <h2>Level changes</h2>
          {!history.data ? <Loading /> : (
            <div className="feed">
              {history.data.transitions.filter((t) => scope === "ward" || t.scope === "city").map((t, i) => (
                <div key={i}><b>{t.date}</b> · {t.name}: {t.from} → <b>{t.to}</b> <span className="muted">— {t.reason}</span></div>
              ))}
            </div>
          )}
        </div>
        <div className="card">
          <h2>Live feed & audit</h2>
          <div className="feed">
            {events.map((e, i) => (
              <div key={`e${i}`}><span className="muted">{e.at}</span> <b>{e.type}</b> {e.type === "transitions" ? `${e.data.length} level change(s)` : e.type === "clock" ? `${e.data.mode} · ${e.data.asOf}${e.data.drill ? ` · drill +${e.data.drill}` : ""}` : e.type === "deliveries" ? `${e.data.length} message(s)` : e.data?.item ?? e.data?.error ?? ""}</div>
            ))}
            {audit.data?.map((a) => (
              <div key={a.id}><span className="muted">{new Date(a.at).toLocaleTimeString()}</span> {a.type}{a.status ? ` · ${a.status}` : ""}{a.item ? ` · ${a.item}` : ""}{a.messages != null ? ` · ${a.messages} msg` : ""}{a.error ? ` · ${String(a.error).slice(0, 80)}` : ""}</div>
            ))}
          </div>
        </div>
      </div>
      <div style={{ marginTop: 14 }}><Subscriptions /></div>
      <div className="card" style={{ marginTop: 14 }}><h2>Delivery log</h2><Deliveries /></div>
    </div>
  );
}
