import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

const dom = new JSDOM(`<!doctype html><body><div id="toasts"></div><div id="host"></div></body>`, { url: "http://localhost/" });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, Node: dom.window.Node, DOMException: dom.window.DOMException });
globalThis.sessionStorage = dom.window.sessionStorage;
const ui = await import("../admin/js/ui.js");
const { ApiError } = await import("../admin/js/api.js");
const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));

test("h() never interprets data as HTML (XSS-safe)", () => {
  const el = ui.h("div", {}, `<img src=x onerror="alert(1)">`);
  assert.equal(el.querySelector("img"), null);
  assert.equal(el.textContent, `<img src=x onerror="alert(1)">`);
  const b = ui.badge("present", `<b>x</b>`); assert.equal(b.querySelector("b"), null);
});

test("badges carry text and an icon, not colour alone", () => {
  for (const s of ["present", "absent", "unmeasurable", "CAMERA_LOST", "ONLINE", "OFFLINE", "ERROR"]) {
    const b = ui.badge(s); assert.ok(b.textContent.trim().length > 2, s); assert.ok(b.querySelector("svg"), s);
  }
});

test("mountAsync: loading -> content", async () => {
  const host = document.getElementById("host");
  let resolve; const p = new Promise((r) => (resolve = r));
  const m = ui.mountAsync(host, () => p, (d) => ui.h("p", { id: "done" }, d.msg));
  assert.ok(host.querySelector("[aria-busy=true]"), "skeleton while loading");
  resolve({ msg: "hello" }); await tick();
  assert.equal(host.querySelector("#done").textContent, "hello"); m.stop();
});

test("mountAsync: error state with working retry, then success", async () => {
  const host = document.getElementById("host"); let n = 0;
  const m = ui.mountAsync(host, async () => { if (n++ === 0) throw new ApiError("", { kind: "network" }); return { ok: 1 }; }, () => ui.h("p", { id: "ok" }, "loaded"));
  await tick();
  assert.match(host.textContent, /Cannot reach the server/); assert.ok(host.querySelector("[role=alert]"));
  host.querySelector("button").click(); await tick(30);
  assert.ok(host.querySelector("#ok")); m.stop();
});

test("mountAsync: a failed background refresh keeps the old content and toasts", async () => {
  const host = document.getElementById("host"); let n = 0;
  const m = ui.mountAsync(host, async () => { if (n++ > 0) throw new ApiError("", { kind: "timeout" }); return {}; }, () => ui.h("p", { id: "keep" }, "stale"));
  await tick(); await m.reload(); await tick();
  assert.ok(host.querySelector("#keep"), "content must not be replaced by an error on refresh");
  assert.match(document.getElementById("toasts").textContent, /too long/); m.stop();
});

test("empty and error helpers render useful text", () => {
  assert.match(ui.emptyState("Nothing", "Add one").textContent, /Nothing/);
  assert.match(ui.errorState(new ApiError("", { kind: "server" })).textContent, /internal error/);
});

test("table builds an accessible table with headers and data labels", () => {
  const t = ui.table([{ label: "A", key: "a" }, { label: "B", render: (r) => r.b * 2 }], [{ a: "x", b: 2 }], { caption: "Demo" });
  assert.equal(t.querySelectorAll("th[scope=col]").length, 2);
  assert.equal(t.querySelector("td[data-label=B]").textContent, "4");
});

test("time formatting uses the server zone, not the browser's", () => {
  ui.setServerTz({ timezone: "Asia/Kolkata", utc_offset_minutes: 330 });
  assert.equal(ui.fmtTime("2026-01-05T08:00:00Z"), "13:30");
  ui.setServerTz({ timezone: null, utc_offset_minutes: -300 });
  assert.equal(ui.fmtTime("2026-01-05T08:00:00Z"), "03:00");
  assert.equal(ui.fmtTime(null), "—"); assert.equal(ui.fmtTime("garbage"), "—");
});

test("confirm dialog resolves false on Cancel and true on Confirm", async () => {
  let p = ui.confirmDialog({ title: "t", message: "m", confirmLabel: "Go" });
  document.querySelector(".modal-foot .btn:not(.btn-primary)").click(); assert.equal(await p, false);
  p = ui.confirmDialog({ title: "t", message: "m", confirmLabel: "Go" });
  document.querySelector(".modal-foot .btn-primary").click(); assert.equal(await p, true);
});

test("a modal without actions renders no stray text (regression: 'null')", () => {
  const m = ui.modal({ title: "No actions", body: ui.h("p", {}, "hello") });
  assert.ok(!document.querySelector(".modal").textContent.includes("null"));
  assert.equal(document.querySelectorAll(".modal-foot").length, 0); m.close();
});
