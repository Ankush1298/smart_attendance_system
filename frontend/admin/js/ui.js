// Small DOM toolkit: safe element builder, icons, badges, states, modal, toast, table helpers.
import { ApiError, humanMessage } from "./api.js";

/** h("div", {class:"x", on:{click}}, "text", child) - text is always a text node (no HTML injection). */
export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "on") for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "vars") for (const [name, val] of Object.entries(v)) el.style.setProperty(name, val);
    else if (k === "value") el.value = v;
    else if (k === "checked" || k === "disabled" || k === "selected" || k === "hidden" || k === "required") el[k] = !!v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  append(el, kids);
  return el;
}
export function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}
export const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };

const ICONS = {
  dashboard: "M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z",
  check: "M5 13l4 4L19 7", live: "M12 8a4 4 0 100 8 4 4 0 000-8zM4.9 4.9a10 10 0 000 14.2M19.1 4.9a10 10 0 010 14.2",
  calendar: "M7 2v3M17 2v3M3 8h18M5 5h14a2 2 0 012 2v12a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2z",
  teacher: "M12 3L2 8l10 5 10-5-10-5zM6 10.5V16c0 1.5 3 3 6 3s6-1.5 6-3v-5.5",
  student: "M16 11a4 4 0 10-8 0 4 4 0 008 0zM4 21a8 8 0 0116 0",
  room: "M4 21V5a2 2 0 012-2h8a2 2 0 012 2v16M2 21h20M9 8h2M9 12h2M9 16h2",
  camera: "M3 8a2 2 0 012-2h2l1.5-2h7L17 6h2a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V8zM12 17a4 4 0 100-8 4 4 0 000 8z",
  report: "M7 3h8l4 4v14H7V3zM14 3v5h5M10 13h6M10 17h6", ready: "M9 12l2 2 4-4M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z",
  settings: "M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z",
  menu: "M4 6h16M4 12h16M4 18h16", close: "M6 6l12 12M18 6L6 18", sun: "M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4M12 8a4 4 0 100 8 4 4 0 000-8z",
  moon: "M21 13A9 9 0 1111 3a7 7 0 0010 10z", alert: "M12 9v4m0 4h.01M10.3 3.9L2.4 18a2 2 0 001.7 3h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z",
  info: "M12 8h.01M11 12h1v5h1M12 21a9 9 0 100-18 9 9 0 000 18z", x: "M6 6l12 12M18 6L6 18", plus: "M12 5v14M5 12h14",
  refresh: "M20 11a8 8 0 10-2.3 5.7M20 4v7h-7", download: "M12 3v12m0 0l-4-4m4 4l4-4M4 20h16", upload: "M12 21V9m0 0l-4 4m4-4l4 4M4 4h16",
  play: "M8 5v14l11-7z", edit: "M4 20h4L19 9l-4-4L4 16v4z", eye: "M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12zM12 15a3 3 0 100-6 3 3 0 000 6z",
  plug: "M9 2v6M15 2v6M6 8h12v4a6 6 0 01-12 0V8zM12 18v4", clock: "M12 7v5l3 2M12 21a9 9 0 100-18 9 9 0 000 18z", inbox: "M3 13l3-9h12l3 9v6H3v-6zm0 0h5l1 3h6l1-3h5",
  key: "M15 7a4 4 0 11-3.5 5.9L4 20.4V22h2v-2h2v-2h2l1.7-1.7A4 4 0 0115 7zM16 9h.01",
  search: "M11 19a8 8 0 100-16 8 8 0 000 16zM21 21l-4.3-4.3", wifi: "M2 9a15 15 0 0120 0M5 12.5a10 10 0 0114 0M8.5 16a5 5 0 017 0M12 19.5h.01",
};
export function icon(name, cls = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none"); svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8"); svg.setAttribute("stroke-linecap", "round"); svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  if (cls) svg.setAttribute("class", cls);
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", ICONS[name] || ICONS.info);
  svg.append(p);
  return svg;
}

// ---- status vocabulary (colour is never the only signal: every badge has an icon and a word)
const STATUS = {
  ACTIVE: ["ok", "Active", "live"], RESUMED: ["ok", "Resumed", "live"], WAITING_TEACHER: ["info", "Waiting for teacher", "clock"],
  TEACHER_ABSENT: ["warn", "Teacher away", "alert"], CAMERA_LOST: ["warn", "Camera lost", "alert"], RECOVERED: ["info", "Recovered", "check"],
  DB_ERROR: ["err", "Database error", "alert"], QUARANTINED: ["err", "Quarantined", "alert"], SUSPENDED: ["err", "Suspended", "alert"],
  COMPLETED: ["", "Completed", "check"], SCHEDULED: ["", "Scheduled", "clock"],
  present: ["ok", "Present", "check"], in_progress: ["info", "In progress", "live"], cancelled: ["warn", "Did not run", "alert"], not_held: ["", "Not recorded", "info"], not_recorded: ["", "Not recorded", "info"], warning: ["warn", "Warning", "alert"], absent: ["err", "Absent", "x"], unmeasurable: ["", "Unmeasurable", "info"],
  partial_absent: ["warn", "Present, long absence", "alert"], grace: ["info", "Grace", "check"],
  ONLINE: ["ok", "Online", "wifi"], OFFLINE: ["err", "Offline", "x"], STALE: ["warn", "Frozen", "alert"], CONNECTING: ["info", "Connecting", "clock"], UNKNOWN: ["", "Not used yet", "info"],
  READY: ["ok", "Ready", "check"], WARNING: ["warn", "Warning", "alert"], ERROR: ["err", "Error", "x"],
  upcoming: ["info", "Upcoming", "clock"], live: ["ok", "Live", "live"], completed: ["", "Completed", "check"], missed: ["warn", "Not recorded", "alert"], starting: ["info", "Starting", "clock"],
};
export function badge(status, label) {
  const [tone, text, ic] = STATUS[status] || ["", String(status ?? "—"), "info"];
  return h("span", { class: "badge " + tone }, icon(ic), label || text);
}

// ---- formatting (all times are shown in the SERVER's time zone: that is the zone timetable times are in)
let tzName = null, tzOffset = null;
export function setServerTz(info) {
  tzName = info?.timezone || null; tzOffset = typeof info?.utc_offset_minutes === "number" ? info.utc_offset_minutes : null;
}
const pad = (n) => String(n).padStart(2, "0");
export function parseTs(v) { if (!v) return null; const d = new Date(v); return isNaN(d) ? null : d; }
function parts(d) {
  if (tzName) {
    try {
      const f = new Intl.DateTimeFormat("en-GB", { timeZone: tzName, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
      const o = Object.fromEntries(f.formatToParts(d).map((p) => [p.type, p.value]));
      return { y: o.year, mo: o.month, d: o.day, h: o.hour, mi: o.minute, s: o.second };
    } catch { /* unknown zone: fall through */ }
  }
  if (tzOffset !== null) { const t = new Date(d.getTime() + tzOffset * 60000); return { y: t.getUTCFullYear(), mo: pad(t.getUTCMonth() + 1), d: pad(t.getUTCDate()), h: pad(t.getUTCHours()), mi: pad(t.getUTCMinutes()), s: pad(t.getUTCSeconds()) }; }
  return { y: d.getFullYear(), mo: pad(d.getMonth() + 1), d: pad(d.getDate()), h: pad(d.getHours()), mi: pad(d.getMinutes()), s: pad(d.getSeconds()) };
}
export const fmtTime = (v) => { const d = parseTs(v); if (!d) return "—"; const p = parts(d); return `${p.h}:${p.mi}`; };
export const fmtTimeS = (v) => { const d = parseTs(v); if (!d) return "—"; const p = parts(d); return `${p.h}:${p.mi}:${p.s}`; };
export const fmtDateTime = (v) => { const d = parseTs(v); if (!d) return "—"; const p = parts(d); return `${p.y}-${p.mo}-${p.d} ${p.h}:${p.mi}`; };
export function relTime(v) {
  const d = parseTs(v); if (!d) return "never";
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 0) return "just now"; if (s < 60) return `${s}s ago`; if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`; return `${Math.floor(s / 86400)} d ago`;
}
export const mins = (n) => (n === null || n === undefined ? "—" : `${(Math.round(n * 10) / 10).toString()} min`);
export const pct = (n) => (n === null || n === undefined ? "—" : `${n}%`);
export const todayStr = () => { const p = parts(new Date()); return `${p.y}-${p.mo}-${p.d}`; };
export const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

// ---- states
export function skeleton(lines = 4) {
  return h("div", { class: "card-body", "aria-busy": "true", "aria-label": "Loading" },
    h("div", { class: "skeleton sk-line short" }), ...Array.from({ length: lines }, () => h("div", { class: "skeleton sk-line" })));
}
export function emptyState(title, text, action) {
  return h("div", { class: "state" }, icon("inbox"), h("h3", {}, title), text ? h("p", {}, text) : null, action || null);
}
export function errorState(err, retry) {
  const msg = humanMessage(err);
  return h("div", { class: "state error", role: "alert" }, icon("alert"), h("h3", {}, "Couldn't load this"), h("p", {}, msg),
    retry ? h("button", { class: "btn", type: "button", on: { click: retry } }, icon("refresh"), "Try again") : null);
}
export function alertBox(tone, title, text, ...extra) {
  return h("div", { class: "alert " + tone, role: tone === "err" ? "alert" : "status" }, icon(tone === "ok" ? "check" : tone === "info" ? "info" : "alert"),
    h("div", { class: "alert-body" }, title ? h("strong", {}, title) : null, text ? h("span", {}, text) : null, ...extra));
}

/**
 * Render async content into `host` with loading / error / (caller handles empty) states and optional polling.
 * Returns {reload, stop}. The previous content stays visible during background refreshes.
 */
export function mountAsync(host, load, render, { poll = 0 } = {}) {
  let stopped = false, timer = null, first = true, seq = 0;
  async function run() {
    const mine = ++seq;
    if (first) { clear(host).append(skeleton()); }
    try {
      const data = await load();
      if (stopped || mine !== seq) return;
      const node = render(data);
      clear(host).append(node);
      first = false;
    } catch (err) {
      if (stopped || mine !== seq || (err instanceof ApiError && err.kind === "cancelled")) return;
      if (first || !host.firstChild) clear(host).append(errorState(err, () => { first = true; run(); }));
      else toast(humanMessage(err), "warn");   // keep stale content, tell the user the refresh failed
    } finally {
      if (!stopped && poll) { timer = setTimeout(() => { if (document.hidden) { schedule(); } else run(); }, poll); }
    }
  }
  function schedule() { timer = setTimeout(run, poll); }
  run();
  return { reload: () => { clearTimeout(timer); return run(); }, stop: () => { stopped = true; clearTimeout(timer); } };
}

// ---- toast
export function toast(message, kind = "info", ms = 5200) {
  const host = document.getElementById("toasts");
  if (!host) return;
  const t = h("div", { class: "toast " + kind, role: kind === "err" ? "alert" : "status" }, icon(kind === "ok" ? "check" : kind === "info" ? "info" : "alert"),
    h("div", {}, String(message)),
    h("button", { class: "icon-btn", type: "button", "aria-label": "Dismiss", on: { click: () => t.remove() } }, icon("x")));
  host.append(t);
  if (ms) setTimeout(() => t.remove(), ms);
}

// ---- modal (focus trap, Esc, restores focus)
export function modal({ title, body, actions = [], wide = false, onClose }) {
  const prev = document.activeElement;
  const titleId = "m" + Math.random().toString(36).slice(2, 8);
  const back = h("div", { class: "modal-back" });
  const dlg = h("div", { class: "modal" + (wide ? " wide" : ""), role: "dialog", "aria-modal": "true", "aria-labelledby": titleId });
  const close = (result) => { document.removeEventListener("keydown", onKey, true); back.remove(); try { prev && prev.focus(); } catch { /* element gone */ } onClose && onClose(result); };
  const head = h("div", { class: "modal-head" }, h("h2", { id: titleId }, title),
    h("button", { class: "icon-btn", type: "button", "aria-label": "Close dialog", on: { click: () => close(null) } }, icon("x")));
  const foot = actions.length ? h("div", { class: "modal-foot" }, ...actions) : null;
  dlg.append(head, h("div", { class: "modal-body" }, body));
  if (foot) dlg.append(foot);
  back.append(dlg);
  back.addEventListener("mousedown", (e) => { if (e.target === back) close(null); });
  function onKey(e) {
    if (e.key === "Escape") { e.stopPropagation(); close(null); return; }
    if (e.key !== "Tab") return;
    const f = [...dlg.querySelectorAll("button,[href],input,select,textarea,[tabindex]:not([tabindex='-1'])")].filter((x) => !x.disabled && x.offsetParent !== null);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
  document.addEventListener("keydown", onKey, true);
  document.body.append(back);
  (dlg.querySelector("input,select,textarea") || dlg.querySelector(".modal-foot .btn-primary") || head.querySelector("button")).focus();
  return { close, el: dlg };
}

export function confirmDialog({ title, message, confirmLabel = "Confirm", danger = false, extra }) {
  return new Promise((resolve) => {
    let m;
    const ok = h("button", { class: "btn " + (danger ? "btn-danger" : "btn-primary"), type: "button", on: { click: () => { m.close(true); } } }, confirmLabel);
    const no = h("button", { class: "btn", type: "button", on: { click: () => m.close(false) } }, "Cancel");
    m = modal({ title, body: h("div", { class: "stack" }, h("p", {}, message), extra || null), actions: [no, ok], onClose: (r) => resolve(r === true) });
  });
}

/** Run an async action on a button: disables it, shows a spinner, reports errors as toasts. */
export function busy(btn, fn) {
  return async (...a) => {
    if (btn.disabled) return;
    btn.disabled = true;
    const old = [...btn.childNodes];
    clear(btn).append(h("span", { class: "spinner", "aria-hidden": "true" }), " Working…");
    try { return await fn(...a); }
    catch (err) { if (!(err instanceof ApiError && err.kind === "cancelled")) toast(humanMessage(err), "err"); }
    finally { clear(btn).append(...old); btn.disabled = false; }
  };
}

// ---- tables
export function table(columns, rows, { rowClass, onRow, caption } = {}) {
  const head = h("tr", {}, ...columns.map((c) => h("th", { scope: "col", class: c.right ? "right" : "" }, c.label)));
  const body = rows.map((r) => {
    const tr = h("tr", { class: [rowClass ? rowClass(r) : "", onRow ? "clickable" : ""].join(" ").trim(), tabindex: onRow ? "0" : null,
      on: onRow ? { click: () => onRow(r), keydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onRow(r); } } } : {} });
    for (const c of columns) tr.append(h("td", { class: (c.right ? "right " : "") + (c.cls || ""), "data-label": c.label || null }, c.render ? c.render(r) : (r[c.key] ?? "—")));
    return tr;
  });
  return h("div", { class: "table-wrap" }, h("table", {}, caption ? h("caption", { class: "sr-only" }, caption) : null, h("thead", {}, head), h("tbody", {}, ...body)));
}

export function field(label, input, hint, error) {
  const id = input.id || (input.id = "f" + Math.random().toString(36).slice(2, 8));
  return h("div", { class: "field" }, h("label", { for: id }, label), input,
    hint ? h("span", { class: "hint", id: id + "-h" }, hint) : null, error ? h("span", { class: "error-text", role: "alert" }, error) : null);
}

export function card(title, body, { actions, flush = true } = {}) {
  return h("section", { class: "card" },
    title ? h("div", { class: "card-head" }, h("h2", {}, title), actions ? h("div", { class: "spacer" }, ...[].concat(actions)) : null) : null,
    h("div", { class: "card-body" + (flush ? " flush" : "") }, body));
}

export async function download(path, filename) {
  const { api } = await import("./api.js");
  const blob = await api(path, { blob: true, timeout: 60000 });
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: filename });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
export { ApiError, humanMessage };
