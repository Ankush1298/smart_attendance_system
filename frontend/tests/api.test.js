import test from "node:test";
import assert from "node:assert/strict";

globalThis.sessionStorage = { _d: {}, getItem(k) { return this._d[k] ?? null; }, setItem(k, v) { this._d[k] = v; }, removeItem(k) { delete this._d[k]; } };
const { api, ApiError, humanMessage, setToken, setUnauthorizedHandler } = await import("../admin/js/api.js");

const resp = (status, body, ct = "application/json") => ({ ok: status >= 200 && status < 300, status, headers: { get: () => ct }, json: async () => body, text: async () => String(body), blob: async () => body });

async function fails(fn) { try { await fn(); } catch (e) { return e; } assert.fail("expected rejection"); }

test("success returns parsed JSON and sends the bearer token", async () => {
  setToken("tok123");
  let seen;
  globalThis.fetch = async (url, o) => { seen = o; return resp(200, { ok: 1 }); };
  assert.deepEqual(await api("/api/x"), { ok: 1 });
  assert.equal(seen.headers.Authorization, "Bearer tok123");
});

for (const [status, kind] of [[401, "auth"], [403, "forbidden"], [404, "notfound"], [409, "conflict"], [422, "validation"], [429, "throttle"], [500, "server"], [503, "server"]]) {
  test(`HTTP ${status} becomes ApiError kind=${kind} with a human message`, async () => {
    setUnauthorizedHandler(() => {});
    globalThis.fetch = async () => resp(status, { error: { code: "c", message: "server said so" } });
    const e = await fails(() => api("/api/x"));
    assert.ok(e instanceof ApiError); assert.equal(e.kind, kind); assert.equal(e.status, status);
    assert.ok(humanMessage(e).length > 10);
    if (kind === "server") assert.ok(!humanMessage(e).includes("server said so"), "5xx details must not be shown raw");
    if (["validation", "conflict"].includes(kind)) assert.equal(humanMessage(e), "server said so");
  });
}

test("401 calls the unauthorized handler (but not for the login request)", async () => {
  let n = 0; setUnauthorizedHandler(() => n++);
  globalThis.fetch = async () => resp(401, { error: { code: "unauthorized", message: "no" } });
  await fails(() => api("/api/x")); assert.equal(n, 1);
  await fails(() => api("/api/auth/login", { method: "POST", json: {}, auth: false })); assert.equal(n, 1);
});

test("network failure", async () => {
  globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
  const e = await fails(() => api("/api/x"));
  assert.equal(e.kind, "network"); assert.match(humanMessage(e), /Cannot reach the server/);
});

test("timeout aborts and is reported as timeout (no infinite spinner)", async () => {
  globalThis.fetch = (url, { signal }) => new Promise((_, rej) => signal.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError"))));
  const t0 = Date.now();
  const e = await fails(() => api("/api/x", { timeout: 80 }));
  assert.equal(e.kind, "timeout"); assert.ok(Date.now() - t0 < 1500); assert.match(humanMessage(e), /too long/);
});

test("non-JSON error body and unreadable success body are handled", async () => {
  globalThis.fetch = async () => ({ ok: false, status: 502, headers: { get: () => "text/html" }, json: async () => { throw new Error("x"); } });
  assert.equal((await fails(() => api("/api/x"))).kind, "server");
  globalThis.fetch = async () => ({ ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => { throw new Error("bad json"); } });
  assert.equal((await fails(() => api("/api/x"))).kind, "server");
});

test("humanMessage never returns an empty string, even for unknown errors", () => {
  assert.ok(humanMessage(new Error("boom")).length > 0);
  assert.ok(humanMessage(new ApiError("", { kind: "client" })).length > 0);
});
