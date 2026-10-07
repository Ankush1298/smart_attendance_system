import { api, setToken, getToken, setUnauthorizedHandler, humanMessage, ApiError } from "./api.js";
import { h, icon, clear, toast, alertBox, field, busy, setServerTz, modal } from "./ui.js";

const STAFF = ["superadmin", "admin"], VIEW = ["superadmin", "admin", "hod"];
export const PAGES = [
  { id: "dashboard", label: "Dashboard", icon: "dashboard", roles: VIEW, mod: () => import("./pages/dashboard.js") },
  { id: "attendance", label: "Attendance", icon: "check", roles: VIEW, mod: () => import("./pages/attendance.js") },
  { id: "live", label: "Live Sessions", icon: "live", roles: VIEW, mod: () => import("./pages/live.js") },
  { id: "timetable", label: "Timetable", icon: "calendar", roles: VIEW, mod: () => import("./pages/timetable.js") },
  { id: "teachers", label: "Teachers", icon: "teacher", roles: VIEW, mod: () => import("./pages/teachers.js") },
  { id: "students", label: "Students", icon: "student", roles: VIEW, mod: () => import("./pages/students.js") },
  { id: "accounts", label: "Accounts & Registration", icon: "key", roles: STAFF, mod: () => import("./pages/accounts.js") },
  { id: "rooms", label: "Rooms", icon: "room", roles: STAFF, mod: () => import("./pages/rooms.js") },
  { id: "cameras", label: "Cameras", icon: "camera", roles: STAFF, mod: () => import("./pages/cameras.js") },
  { id: "reports", label: "Reports", icon: "report", roles: VIEW, mod: () => import("./pages/reports.js") },
  { id: "readiness", label: "System Readiness", icon: "ready", roles: VIEW, mod: () => import("./pages/readiness.js") },
  { id: "settings", label: "Settings", icon: "settings", roles: STAFF, mod: () => import("./pages/settings.js") },
  { id: "my-classes", label: "My Classes", icon: "calendar", roles: ["teacher"], mod: () => import("./pages/teacher-home.js") },
  { id: "my-attendance", label: "My Attendance", icon: "check", roles: ["student"], mod: () => import("./pages/student-home.js") },
  { id: "register-face", label: "Register My Face", icon: "camera", roles: ["teacher", "student"], mod: () => import("./pages/register-face.js") },
];
export const ROLE_LABEL = { superadmin: "Super admin", admin: "Admin", hod: "HOD / Management", teacher: "Teacher", student: "Student" };
const HOME = { superadmin: "dashboard", admin: "dashboard", hod: "dashboard", teacher: "my-classes", student: "my-attendance" };
let me = { role: null };
const allowedPages = () => PAGES.filter((p) => p.roles.includes(me.role));


const $ = (id) => document.getElementById(id);
let cleanup = null, navToken = 0, statusTimer = null;

// ---------- theme
function applyTheme(t) {
  if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t); else document.documentElement.removeAttribute("data-theme");
  const dark = t === "dark" || (t !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  const b = $("theme-btn"); if (b) { clear(b).append(icon(dark ? "sun" : "moon")); b.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode"); }
}
function initTheme() {
  let t = null; try { t = localStorage.getItem("sa_theme"); } catch { /* storage blocked */ }
  applyTheme(t);
  $("theme-btn").addEventListener("click", () => {
    const dark = document.documentElement.getAttribute("data-theme") === "dark" || (!document.documentElement.getAttribute("data-theme") && matchMedia("(prefers-color-scheme: dark)").matches);
    const next = dark ? "light" : "dark"; try { localStorage.setItem("sa_theme", next); } catch { /* ignore */ }
    applyTheme(next);
  });
}

// ---------- shell
function buildNav() {
  const nav = clear($("nav"));
  for (const p of allowedPages()) nav.append(h("a", { href: "#/" + p.id, dataset: { page: p.id } }, icon(p.icon), h("span", {}, p.label), p.id === "readiness" ? h("span", { class: "nav-count", id: "ready-count", hidden: true }) : null));
}
function setMenu(open) {
  $("sidebar").classList.toggle("open", open); $("scrim").hidden = !open; $("menu-btn").setAttribute("aria-expanded", String(open));
}

async function route() {
  const id = (location.hash.replace(/^#\/?/, "").split(/[/?]/)[0]) || HOME[me.role];
  const pages = allowedPages();
  const page = pages.find((p) => p.id === id) || pages.find((p) => p.id === HOME[me.role]) || pages[0];
  const mine = ++navToken;
  if (cleanup) { try { cleanup(); } catch { /* page cleanup must not block navigation */ } cleanup = null; }
  for (const a of document.querySelectorAll("#nav a")) {
    if (a.dataset.page === page.id) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  $("page-title").textContent = page.label;
  document.title = page.label + " · Smart Attendance";
  setMenu(false);
  const host = clear($("main"));
  try {
    const mod = await page.mod();
    if (mine !== navToken) return;
    cleanup = (await mod.mount(host, { navigate: (to) => { location.hash = "#/" + to; }, refreshStatus, role: me.role, user: me, canEdit: STAFF.includes(me.role) })) || null;
  } catch (err) {
    if (mine !== navToken) return;
    console.error(err);
    // A module that cannot be fetched (TypeError) almost always means the server is unreachable.
    const unreachable = !(err instanceof ApiError) && err instanceof TypeError;
    const text = err instanceof ApiError ? humanMessage(err) : unreachable
      ? "Cannot reach the server. Check that it is running and that you are connected, then try again."
      : "An unexpected error occurred while drawing the page. Reload to try again.";
    clear(host).append(h("div", { class: "state error", role: "alert" }, icon("alert"), h("h3", {}, "This page could not be opened"), h("p", {}, text),
      h("button", { class: "btn", type: "button", on: { click: () => (unreachable ? location.reload() : route()) } }, icon("refresh"), "Try again")));   // a failed module import is cached by the browser: only a reload retries it
  }
  $("page-title").focus({ preventScroll: true });
}

// ---------- system status (banner, sidebar pill, readiness badge)
export async function refreshStatus() {
  if (!VIEW.includes(me.role)) { clear($("engine-pill")); return; }
  try {
    const rd = await api("/api/readiness", { timeout: 15000 });
    const pill = clear($("engine-pill"));
    const tone = rd.overall === "READY" ? "ok" : rd.overall === "WARNING" ? "warn" : "err";
    pill.append(h("span", { class: "dot " + tone }), h("span", {}, rd.automatic_attendance_allowed ? (rd.overall === "READY" ? "Automatic attendance on" : "Running · needs attention") : "Automatic attendance paused"));
    const errs = rd.checks.filter((c) => c.status === "ERROR").length;
    const cnt = $("ready-count"); if (cnt) { cnt.hidden = !errs; cnt.textContent = String(errs); }
    const banner = clear($("banner"));
    if (!rd.automatic_attendance_allowed) {
      const what = rd.checks.filter((c) => c.mandatory && c.status === "ERROR").map((c) => c.label).join(", ");
      banner.append(h("div", { class: "alert err", role: "alert" }, icon("alert"), h("div", { class: "alert-body" },
        h("strong", {}, "Automatic attendance is paused"), h("span", {}, `Required: ${what}. `), h("a", { href: "#/readiness" }, "See what to fix"))));
    }
  } catch (err) {
    if (err instanceof ApiError && err.kind === "auth") return;
    const pill = clear($("engine-pill")); pill.append(h("span", { class: "dot err" }), h("span", {}, "Server unreachable"));
  }
}

function showApp(user) {
  me = { id: user.id, name: user.name, role: user.role };
  setServerTz(user);
  $("login").hidden = true; $("app").hidden = false;
  $("user-name").textContent = `${user?.name || ""} · ${ROLE_LABEL[user?.role] || ""}`;
  buildNav();
  route();
  refreshStatus();
  clearInterval(statusTimer); statusTimer = setInterval(() => { if (!document.hidden) refreshStatus(); }, 30000);
}

function showLogin(message) {
  $("app").hidden = true;
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  clearInterval(statusTimer);
  setToken(null);
  const host = clear($("login")); host.hidden = false;
  const idIn = h("input", { type: "text", id: "login-id", autocomplete: "username", required: true, autofocus: true });
  const pwIn = h("input", { type: "password", id: "login-pw", autocomplete: "current-password", required: true });
  const msg = h("div", { id: "login-msg" }, message ? alertBox("warn", null, message) : null);
  const btn = h("button", { class: "btn btn-primary", type: "submit" }, "Sign in");
  const form = h("form", { novalidate: true }, msg, field("ID", idIn), field("Password", pwIn), h("div", {}, btn));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    clear(msg);
    if (!idIn.value.trim() || !pwIn.value) { msg.append(alertBox("err", null, "Enter your ID and password.")); (idIn.value.trim() ? pwIn : idIn).focus(); return; }
    btn.disabled = true; clear(btn).append(h("span", { class: "spinner" }), " Signing in…");
    try {
      const r = await api("/api/auth/login", { method: "POST", json: { id: idIn.value.trim(), password: pwIn.value }, auth: false });
      setToken(r.token); pwIn.value = "";
      showApp(r);
    } catch (err) {
      const text = err instanceof ApiError && err.kind === "auth" ? (err.message || "Incorrect ID or password.") : humanMessage(err);
      msg.append(alertBox("err", null, text)); pwIn.select();
    } finally { btn.disabled = false; clear(btn).append("Sign in"); }
  });
  const reg = h("p", { class: "muted small", id: "reg-link" });
  api("/api/registration-info", { auth: false, timeout: 6000 }).then(async (r) => {
    if (!r.enabled) return;
    const { registerCard } = await import("./pages/register-face.js");
    const open = (role) => (e) => { e.preventDefault(); modal({ title: "Register your face", body: registerCard(role, r) }); };
    reg.append("New here? Register your face as a ", h("a", { href: r.student_url, id: "reg-student", on: { click: open("student") } }, "student"), " or ", h("a", { href: r.teacher_url, id: "reg-teacher", on: { click: open("teacher") } }, "teacher"), " (QR code for your phone).");
  }).catch(() => {});
  host.append(h("div", { class: "card login-card" }, h("div", { class: "brand" }, h("span", { class: "brand-mark" }), h("span", { class: "brand-name" }, "Smart Attendance")),
    h("h1", {}, "Sign in"), h("p", { class: "lead" }, "Use your ID and password. Students, teachers, HODs and administrators all sign in here."), form, reg));
  idIn.focus();
}

async function boot() {
  initTheme();
  setUnauthorizedHandler(() => showLogin("Your session has expired. Please sign in again."));
  $("menu-btn").append(icon("menu"));
  $("menu-btn").addEventListener("click", () => setMenu(!$("sidebar").classList.contains("open")));
  $("scrim").addEventListener("click", () => setMenu(false));
  $("logout-btn").addEventListener("click", () => { showLogin(); toast("Signed out.", "info", 2500); });
  addEventListener("hashchange", () => { if (!$("app").hidden) route(); });
  addEventListener("keydown", (e) => { if (e.key === "Escape") setMenu(false); });
  if (!getToken()) return showLogin();
  try { const me = await api("/api/auth/me", { timeout: 10000 }); showApp(me); }
  catch (err) { if (err instanceof ApiError && err.kind === "network") { showLogin("Cannot reach the server. Check that it is running, then reload."); } else showLogin(); }
}
boot();
