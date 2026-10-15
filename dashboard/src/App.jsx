import { useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { AppProvider, useApp } from "./lib/state.jsx";
import { getApiKey, setApiKey } from "./lib/api.js";
import MapPage from "./pages/MapPage.jsx";
import CommandPage from "./pages/CommandPage.jsx";
import PlanPage from "./pages/PlanPage.jsx";
import EvidencePage from "./pages/EvidencePage.jsx";
import FieldPage from "./pages/FieldPage.jsx";

function Clock() {
  const { clock, setAsOf, setWindow, step, togglePlay, playing, goLive, refreshLive, setDrill } = useApp();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [episodes, setEpisodes] = useState(null);
  if (!clock) return null;
  const live = clock.mode === "live";
  const run = (fn) => async () => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  const loadEpisodes = () => !episodes && fetch("/api/episodes").then((r) => r.json()).then(setEpisodes).catch(() => {});
  return (
    <div className="clock" role="group" aria-label="Forecast clock">
      <span className={`mode ${live ? "live" : "replay"}`}>{live ? "Live" : "Replay"}</span>
      {!live && (
        <select aria-label="Replay a heat episode" value={clock.window?.episode ?? ""} onFocus={loadEpisodes} onMouseDown={loadEpisodes}
          onChange={(e) => {
            const ep = episodes?.find((x) => x.id === e.target.value);
            if (ep) run(() => setWindow({ start: ep.start, end: ep.end, episode: ep.id }))();
          }} disabled={busy}>
          <option value="">{clock.window?.episode ? clock.window.episode : `${clock.window.start} → ${clock.window.end}`}</option>
          {(episodes ?? []).map((e) => <option key={e.id} value={e.id}>{e.label}</option>)}
        </select>
      )}
      <button className="btn sm" onClick={run(() => step(-1))} disabled={busy || live} aria-label="Previous day">◀</button>
      <select value={clock.asOf} onChange={(e) => run(() => setAsOf(e.target.value))()} disabled={busy} aria-label="Issued on">
        {clock.dates.map((d) => <option key={d} value={d}>{live && d === clock.dates.at(-1) ? `Today ${d}` : `Issued ${d}`}</option>)}
      </select>
      <button className="btn sm" onClick={run(() => step(1))} disabled={busy || live} aria-label="Next day">▶</button>
      {!live && <button className="btn sm" onClick={togglePlay} aria-pressed={playing}>{playing ? "Pause" : "Play"}</button>}
      <button className="btn sm" onClick={run(live ? refreshLive : goLive)} disabled={busy}
        title={clock.liveError ? `Last live fetch failed: ${clock.liveError}` : "Today's NWP forecast through the same pipeline"}>
        {busy ? "…" : live ? "Refresh" : clock.liveAvailable ? "Go live" : "Fetch live"}
      </button>
      <label className="small row" style={{ gap: 4 }} title="Labelled heatwave drill: shifts air temperature (and dewpoint) by this much">
        Drill
        <select value={clock.drill} onChange={(e) => run(() => setDrill(Number(e.target.value)))()} disabled={busy} aria-label="Drill offset">
          {[0, 1, 2, 3, 4].map((x) => <option key={x} value={x}>{x ? `+${x} °C` : "off"}</option>)}
        </select>
      </label>
      {err && <span className="small" style={{ color: "var(--band-emergency)" }} role="alert">{err}</span>}
    </div>
  );
}

function KeyButton() {
  const [open, setOpen] = useState(false);
  const [v, setV] = useState(getApiKey());
  return (
    <span style={{ position: "relative" }}>
      <button className="btn sm" onClick={() => setOpen(!open)} aria-expanded={open}>Operator key</button>
      {open && (
        <div className="card" style={{ position: "absolute", right: 0, top: 36, zIndex: 1000, width: 280 }}>
          <label className="field">
            API key for sending alerts and moving the clock
            <input type="password" value={v} onChange={(e) => setV(e.target.value)} />
          </label>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn sm primary" onClick={() => { setApiKey(v); setOpen(false); }}>Save</button>
            <span className="small muted">Stored in this browser only.</span>
          </div>
        </div>
      )}
    </span>
  );
}

function Notice() {
  const { error, clock } = useApp();
  if (error) {
    return <div className="notice"><span style={{ color: "var(--band-emergency)" }}>Cannot reach the UshmaLens API ({error}). Start it with <code>python -m ushmalens</code>.</span></div>;
  }
  if (!clock) return null;
  const s = clock.sources ?? {};
  return (
    <div className="notice" role="note">
      {clock.mode === "live" ? (
        <span><b>Live</b> · {s.weather}{s.weatherStale ? " (stale cache)" : ""} · uncertainty: {s.uncertainty}. CAP status <i>Actual</i>.</span>
      ) : (
        <span><b>Historical replay</b> · {s.weather}. Forecasts are the observed weather with lead-time error bands. CAP status <i>Exercise</i>.</span>
      )}
      {clock.drill ? <span>· <b style={{ color: "var(--band-emergency)" }}>DRILL +{clock.drill} °C</b> — not real conditions</span> : null}
      {clock.mode !== "live" && clock.liveError && <span className="muted">· live feed: {clock.liveError.slice(0, 120)}</span>}
      <span className="muted">· Heat–mortality curves are anchored to published relative risks; see Evidence.</span>
    </div>
  );
}

function Shell() {
  const loc = useLocation();
  if (loc.pathname.startsWith("/field")) return <FieldPage />;
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <img src="/icon.svg" alt="" />
          <span>UshmaLens <small>Heat-health early warning · 4 cities · 417 wards</small></span>
        </div>
        <nav className="nav" aria-label="Sections">
          <NavLink to="/" end>Map</NavLink>
          <NavLink to="/command">Command</NavLink>
          <NavLink to="/plan">Plan & simulate</NavLink>
          <NavLink to="/evidence">Evidence</NavLink>
          <NavLink to="/field">Field view</NavLink>
        </nav>
        <div className="spacer" />
        <Clock />
        <KeyButton />
      </header>
      <Notice />
      <main>
        <Routes>
          <Route path="/" element={<MapPage />} />
          <Route path="/command" element={<CommandPage />} />
          <Route path="/plan" element={<PlanPage />} />
          <Route path="/evidence" element={<EvidencePage />} />
          <Route path="*" element={<div className="page"><h1>Not found</h1></div>} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  );
}
