// App-wide state: server meta, the clock (live / replay / drill), the selected
// city and the server-sent event stream.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "./api.js";

const Ctx = createContext(null);
export const useApp = () => useContext(Ctx);

function readPref(k, d) {
  try {
    return localStorage.getItem(k) ?? d;
  } catch {
    return d;
  }
}
function writePref(k, v) {
  try {
    localStorage.setItem(k, v);
  } catch {
    /* storage unavailable */
  }
}

export function AppProvider({ children }) {
  const [meta, setMeta] = useState(null);
  const [clock, setClock] = useState(null);
  const [version, setVersion] = useState(0);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState(null);
  const [city, setCityState] = useState(() => readPref("ushmalens.city", "India"));
  const [playing, setPlaying] = useState(false);
  const playRef = useRef(null);

  const setCity = useCallback((c) => {
    setCityState(c);
    writePref("ushmalens.city", c);
  }, []);

  const load = useCallback(async () => {
    try {
      const [m, c] = await Promise.all([api.get("/meta"), api.get("/clock")]);
      setMeta(m);
      setClock(c);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const applyClock = useCallback((c) => {
    setClock(c);
    setVersion((v) => v + 1);
  }, []);

  useEffect(() => {
    let es;
    try {
      es = new EventSource("/api/stream");
    } catch {
      return undefined;
    }
    const push = (type) => (ev) => {
      let data = null;
      try {
        data = JSON.parse(ev.data);
      } catch {
        return;
      }
      setEvents((xs) => [{ type, data, at: new Date().toLocaleTimeString() }, ...xs].slice(0, 150));
      if (type === "clock") applyClock(data);
      if (type === "live") api.get("/clock").then(setClock).catch(() => {});
      if (type === "deliveries" || type === "hap") setVersion((v) => v + 1);
    };
    for (const t of ["clock", "transitions", "deliveries", "hap", "live"]) es.addEventListener(t, push(t));
    return () => es.close();
  }, [applyClock]);

  const call = useCallback(async (path, body) => applyClock(await api.post(path, body)), [applyClock]);
  const setAsOf = useCallback((date) => call("/clock/asof", { date }), [call]);
  const setWindow = useCallback((w) => call("/clock/window", w), [call]);
  const goLive = useCallback(() => call(clock?.liveAvailable ? "/live/use" : "/live/refresh"), [call, clock]);
  const refreshLive = useCallback(() => call("/live/refresh"), [call]);
  const setDrill = useCallback((offset) => call("/drill", { offset }), [call]);

  const step = useCallback(
    async (delta) => {
      if (!clock) return false;
      const i = clock.dates.indexOf(clock.asOf) + delta;
      if (i < 0 || i >= clock.dates.length) return false;
      await setAsOf(clock.dates[i]);
      return true;
    },
    [clock, setAsOf],
  );
  const stepRef = useRef(step);
  stepRef.current = step;

  const togglePlay = useCallback(() => {
    if (playRef.current) {
      clearInterval(playRef.current);
      playRef.current = null;
      setPlaying(false);
      return;
    }
    setPlaying(true);
    playRef.current = setInterval(async () => {
      const ok = await stepRef.current(1).catch(() => false);
      if (!ok) {
        clearInterval(playRef.current);
        playRef.current = null;
        setPlaying(false);
      }
    }, 2200);
  }, []);
  useEffect(() => () => playRef.current && clearInterval(playRef.current), []);

  return (
    <Ctx.Provider value={{ meta, clock, version, events, error, city, setCity, setAsOf, setWindow, goLive, refreshLive,
      setDrill, step, togglePlay, playing, reload: load, bump: () => setVersion((v) => v + 1) }}>
      {children}
    </Ctx.Provider>
  );
}

/** Fetch a path whenever the app data version changes. */
export function useData(path, deps = [], auth = false) {
  const { version } = useApp();
  const [state, setState] = useState({ data: null, error: null, loading: true });
  useEffect(() => {
    if (!path) return undefined;
    let alive = true;
    setState((s) => ({ ...s, loading: true }));
    (auth ? api.getAuth(path) : api.get(path))
      .then((data) => alive && setState({ data, error: null, loading: false }))
      .catch((error) => alive && setState({ data: null, error: error.message, loading: false }));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, version, ...deps]);
  return state;
}
