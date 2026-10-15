// Field view for ASHA and anganwadi workers: one ward, today's level, three
// things to do, in the worker's language. Large type, works on a small phone,
// and the service worker keeps the last-loaded advisory available offline.

import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, wardPath } from "../lib/api.js";
import { BAND_STYLE } from "../lib/bands.js";

const UI = {
  en: { ward: "Ward", todo: "What to do", share: "Copy SMS", copied: "Copied", offline: "Offline — showing last saved data", peak: "Hottest day" },
  hi: { ward: "वार्ड", todo: "क्या करें", share: "SMS कॉपी करें", copied: "कॉपी हो गया", offline: "ऑफ़लाइन — अंतिम सहेजा डेटा", peak: "सबसे अधिक गर्मी" },
  mr: { ward: "वॉर्ड", todo: "काय करावे", share: "SMS कॉपी करा", copied: "कॉपी झाले", offline: "ऑफलाइन — शेवटचा जतन केलेला डेटा", peak: "सर्वाधिक उष्ण दिवस" },
  gu: { ward: "વોર્ડ", todo: "શું કરવું", share: "SMS કૉપિ કરો", copied: "કૉપિ થયું", offline: "ઑફલાઇન — છેલ્લો સાચવેલ ડેટા", peak: "સૌથી ગરમ દિવસ" },
  ta: { ward: "வார்டு", todo: "என்ன செய்ய வேண்டும்", share: "SMS நகலெடு", copied: "நகலெடுக்கப்பட்டது", offline: "இணைப்பில்லை — கடைசியாகச் சேமித்த தரவு", peak: "அதிக வெப்ப நாள்" },
  te: { ward: "వార్డు", todo: "ఏమి చేయాలి", share: "SMS కాపీ చేయండి", copied: "కాపీ అయింది", offline: "ఆఫ్‌లైన్ — చివరిగా సేవ్ చేసిన డేటా", peak: "అత్యంత వేడి రోజు" },
  or: { ward: "ୱାର୍ଡ", todo: "କଣ କରିବେ", share: "SMS କପି କରନ୍ତୁ", copied: "କପି ହେଲା", offline: "ଅଫଲାଇନ — ଶେଷ ସଞ୍ଚିତ ତଥ୍ୟ", peak: "ସର୍ବାଧିକ ଗରମ" },
};

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

export default function FieldPage() {
  const [params, setParams] = useSearchParams();
  const [meta, setMeta] = useState(null);
  const [wards, setWards] = useState([]);
  const wardKey = params.get("ward") || readPref("ushmalens.field.ward", "Ahmedabad:1");
  const city = wardKey.split(":")[0];
  const [lang, setLang] = useState(() => readPref("ushmalens.field.lang", ""));
  const [data, setData] = useState(null);
  const [offline, setOffline] = useState(!navigator.onLine);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api.get("/meta").then(setMeta).catch(() => {});
    const on = () => setOffline(false);
    const off = () => setOffline(true);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);
  useEffect(() => {
    api.get(`/cities/${city}/wards`).then(setWards).catch(() => {});
  }, [city]);

  const cityLang = meta?.cities.find((c) => c.city === city)?.lang ?? "hi";
  const L = lang && [cityLang, "hi", "en"].includes(lang) ? lang : cityLang;
  useEffect(() => {
    writePref("ushmalens.field.ward", wardKey);
    if (lang) writePref("ushmalens.field.lang", lang);
    api.get(`/field/${wardPath(wardKey)}?lang=${L}`).then(setData).catch(() => setData(null));
  }, [wardKey, L, lang]);

  const t = UI[L] ?? UI.en;
  const s = BAND_STYLE[data?.level ?? "Normal"];
  const langs = useMemo(() => (meta?.languages ?? []).filter((l) => [cityLang, "hi", "en"].includes(l.code)), [meta, cityLang]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(data.sms ?? "");
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };

  return (
    <div className="field" lang={L}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <Link to="/" className="brand" style={{ textDecoration: "none", color: "inherit" }}>
          <img src="/icon.svg" alt="" width="26" height="26" /> UshmaLens
        </Link>
        <div className="seg" role="group" aria-label="Language">
          {langs.map((l) => <button key={l.code} aria-pressed={L === l.code} onClick={() => setLang(l.code)}>{l.name}</button>)}
        </div>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <label className="field" style={{ flex: 1 }}>
          City
          <select value={city} onChange={(e) => setParams({ ward: `${e.target.value}:${e.target.value === "Mumbai" ? "A" : e.target.value === "Chennai" ? "0" : "1"}` })} style={{ fontSize: 17, padding: 10 }}>
            {(meta?.cities ?? []).map((c) => <option key={c.city}>{c.city}</option>)}
          </select>
        </label>
        <label className="field" style={{ flex: 2 }}>
          {t.ward}
          <select value={wardKey} onChange={(e) => setParams({ ward: e.target.value })} style={{ fontSize: 17, padding: 10 }}>
            {wards.map((w) => <option key={w.key} value={w.key}>{w.name}</option>)}
          </select>
        </label>
      </div>
      {offline && <div className="sentence small" role="status">{t.offline}</div>}
      {data && (
        <>
          <div className="hero" style={{ background: s.hex }} role="status" aria-live="polite">
            <div className="lvl"><span aria-hidden="true">{s.icon} </span>{data.levelLabel}</div>
            <div className="sub">
              {data.days.slice(0, 3).map((d, i) => <span key={d.date}>{i ? " · " : ""}{d.date.slice(5)}: {BAND_STYLE[d.band].icon} {d.label} ({Math.round(d.tmax)}°)</span>)}
            </div>
            <div className="sub">{t.peak}: {data.peak.date} ({data.peak.label})</div>
          </div>
          <h2 style={{ fontSize: 18, marginBottom: 0 }}>{t.todo}</h2>
          <ol>{data.actions.slice(0, 4).map((a) => <li key={a}>{a}</li>)}</ol>
          {data.emergency && data.level !== "Normal" && <div className="sms" style={{ borderLeft: "5px solid var(--band-emergency)", marginBottom: 10 }}>{data.emergency.signs} <b>{data.emergency.call}</b></div>}
          {data.sms && (
            <>
              <div className="sms">{data.sms}</div>
              <button className="btn primary" style={{ marginTop: 10, fontSize: 17, padding: "10px 16px" }} onClick={copy}>{copied ? t.copied : t.share}</button>
            </>
          )}
          <p className="small muted" style={{ marginTop: 16 }}>{data.name} · {data.city} · {data.mode === "live" ? "today" : "replay"} {data.asOf} · {data.review}</p>
        </>
      )}
    </div>
  );
}
