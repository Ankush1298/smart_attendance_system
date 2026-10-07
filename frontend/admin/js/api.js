// Thin fetch wrapper: timeouts, normalised errors, bearer token, 401 handling.
const TOKEN_KEY = "sa_token";
let memToken = null;
let onUnauthorized = () => {};

export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export function getToken() {
  if (memToken) return memToken;
  try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
}
export function setToken(t) {
  memToken = t || null;
  try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* storage may be blocked */ }
}

export class ApiError extends Error {
  constructor(message, { status = 0, code = "error", kind = "server" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status; this.code = code; this.kind = kind;
  }
}

const KIND = { 401: "auth", 403: "forbidden", 404: "notfound", 409: "conflict", 413: "validation", 422: "validation", 429: "throttle" };

/** Human-readable explanation for any failure. Never returns an empty string. */
export function humanMessage(err) {
  if (!(err instanceof ApiError)) return "Something unexpected went wrong. Please try again.";
  if (err.code === "db_unavailable") return err.message;
  switch (err.kind) {
    case "network": return "Cannot reach the server. Check that it is running and that you are connected, then retry.";
    case "timeout": return "The server took too long to answer. It may be busy; please retry in a moment.";
    case "auth": return err.message || "Your session has expired. Please sign in again.";
    case "forbidden": return err.message || "You do not have permission to do that.";
    case "notfound": return err.message || "That item no longer exists.";
    case "server": return "The server reported an internal error. The details are in the server log.";
    default: return err.message || "The request could not be completed.";
  }
}

/**
 * api("/api/x", { method, json, form, timeout, blob, auth })
 * Resolves with parsed JSON (or Blob when blob:true). Rejects with ApiError.
 */
export async function api(path, { method = "GET", json, form, timeout = 20000, blob = false, auth = true, signal } = {}) {
  const headers = {};
  const tok = getToken();
  if (auth && tok) headers.Authorization = "Bearer " + tok;
  let body;
  if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  else if (form) body = form;

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort("timeout"), timeout);
  if (signal) signal.addEventListener("abort", () => ctrl.abort("cancelled"), { once: true });
  let res;
  try {
    res = await fetch(path, { method, headers, body, signal: ctrl.signal, cache: "no-store" });
  } catch (e) {
    if (ctrl.signal.reason === "cancelled") throw new ApiError("Request cancelled.", { kind: "cancelled" });
    throw new ApiError(ctrl.signal.aborted ? "timeout" : "network", { kind: ctrl.signal.aborted ? "timeout" : "network" });
  } finally {
    clearTimeout(timer);
  }
  if (res.ok) {
    if (blob) return res.blob();
    if (res.status === 204) return {};
    const ct = res.headers.get("content-type") || "";
    if (!ct.includes("json")) return res.text();
    try { return await res.json(); } catch { throw new ApiError("The server sent an unreadable response.", { status: res.status, kind: "server" }); }
  }
  let code = "error", message = "";
  try { const j = await res.json(); code = j?.error?.code || code; message = j?.error?.message || ""; } catch { /* non-JSON error body */ }
  const kind = KIND[res.status] || (res.status >= 500 ? "server" : "client");
  const err = new ApiError(message, { status: res.status, code, kind });
  if (res.status === 401 && auth && path !== "/api/auth/login") onUnauthorized(err);
  throw err;
}
