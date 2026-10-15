// API client. The operator key for write routes is a per-viewer convenience
// kept in localStorage (wrapped: storage can be unavailable in private windows).

const KEY = "ushmalens.apiKey";
const DEFAULT = "ushmalens-dev-key";

export function getApiKey() {
  try {
    return localStorage.getItem(KEY) || DEFAULT;
  } catch {
    return DEFAULT;
  }
}

export function setApiKey(v) {
  try {
    localStorage.setItem(KEY, v);
  } catch {
    /* storage unavailable: key lasts for this page only */
  }
}

async function request(method, path, body, auth = method !== "GET") {
  const res = await fetch(`/api${path}`, {
    method,
    headers: { "content-type": "application/json", ...(auth ? { "x-api-key": getApiKey() } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data = text;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* not JSON */
  }
  if (!res.ok) throw new Error(data?.detail ?? data?.error ?? `HTTP ${res.status}`);
  return data;
}

export const api = {
  get: (p) => request("GET", p),
  getAuth: (p) => request("GET", p, undefined, true),
  post: (p, b) => request("POST", p, b ?? {}),
  del: (p) => request("DELETE", p),
};

/** Ward ids can contain "/" (Mumbai "F/N"): encode each segment. */
export const wardPath = (key) => {
  const [city, ...rest] = key.split(":");
  return `${encodeURIComponent(city)}/${rest.join(":").split("/").map(encodeURIComponent).join("/")}`;
};
