import { api, humanMessage } from "../api.js";
import { registerCard } from "./register-face.js";
import { h, icon, badge, card, table, mountAsync, emptyState, alertBox, toast, fmtDateTime, mins, pct } from "../ui.js";

const TONE = { present: "ok", warning: "warn", absent: "err", unmeasurable: "info", in_progress: "info", cancelled: "warn", upcoming: "info", not_held: "info", not_recorded: "info" };

function seenKey(id) { return "sa_seen_" + id; }
function loadSeen(id) { try { return new Set(JSON.parse(localStorage.getItem(seenKey(id)) || "[]")); } catch { return new Set(); } }
function saveSeen(id, set) { try { localStorage.setItem(seenKey(id), JSON.stringify([...set].slice(-200))); } catch { /* storage blocked: notifications may repeat */ } }
const FINAL = new Set(["present", "warning", "absent", "unmeasurable", "cancelled"]);

/** A small notification for every class whose result appeared since the student last looked. */
function notify(me, today, firstLoad) {
  const seen = loadSeen(me), fresh = [];
  for (const c of today.items) if (FINAL.has(c.status) && !seen.has(c.session_id)) { fresh.push(c); seen.add(c.session_id); }
  saveSeen(me, seen);
  for (const c of fresh) {
    toast(c.message, c.status === "present" ? "ok" : c.status === "absent" ? "err" : "warn", 12000);
    try { if ("Notification" in window && Notification.permission === "granted" && !firstLoad) new Notification("Attendance", { body: c.message }); } catch { /* not supported */ }
  }
}

function classCard(c) {
  const p = c.attendance_percent;
  const tone = p === null ? "" : p >= 80 ? "" : p >= 60 ? "warn" : "err";
  return h("article", { class: "card live-card", "aria-label": c.subject },
    h("header", { class: "split" }, h("div", {}, h("h3", {}, c.subject), h("div", { class: "muted small" }, `${c.classes_held} class(es) held`)),
      h("div", { class: "kpi" }, h("div", { class: "value" }, p === null ? "—" : p + "%"))),
    h("div", { class: "progress " + tone, role: "img", "aria-label": `Attendance ${p === null ? "not available" : p + " percent"}` }, h("span", { vars: { "--w": (p || 0) + "%" } })),
    h("div", { class: "row" }, badge("present", `${c.present} present`), c.warning ? badge("warning", `${c.warning} partial`) : null, badge("absent", `${c.absent} absent`), c.unmeasurable ? badge("unmeasurable", `${c.unmeasurable} not measured`) : null),
    c.average_presence_percent !== null ? h("div", { class: "pill-note" }, `On average you were in the room for ${c.average_presence_percent}% of each class.`) : null);
}

function render(d, state) {
  const { profile, summary, today } = d;
  notify(profile.id, today, state.first);
  state.first = false;
  const reg = state.reg;
  const kids = [];
  if (!profile.face_registered) {
    kids.push(alertBox("warn", "Your face is not registered yet", "The cameras cannot mark you present until you register. It takes about a minute: scan the code below with your phone."));
    if (reg) kids.push(card(null, h("div", { class: "card-body" }, registerCard("student", reg)), { flush: false }));
  }
  if (!profile.sections.length) kids.push(alertBox("info", "You are not in a section yet", "Attendance is recorded for the classes of your section. Ask your administrator to add you."));

  kids.push(card("Today", today.items.length ? h("div", { class: "stack notice-list" }, ...today.items.map((c) => h("div", { class: "alert " + (TONE[c.status] || "info"), role: "status" },
    icon(c.status === "present" ? "check" : c.status === "absent" ? "x" : c.status === "upcoming" || c.status === "in_progress" ? "clock" : "alert"),
    h("div", { class: "alert-body" }, h("strong", {}, `${c.subject} · ${c.start_time}–${c.end_time} · Room ${c.room_id}`), c.message, " ", badge(c.status))))) : emptyState("No classes today", today.has_sections ? "Nothing is scheduled for your section today." : "You are not in a section yet."), { flush: false }));

  kids.push(h("div", { class: "grid kpis" },
    h("div", { class: "card kpi" }, h("div", { class: "label" }, icon("check"), "Overall attendance"), h("div", { class: "value" }, pct(summary.overall_percent)), h("div", { class: "sub" }, `${summary.classes_counted} class(es) counted`)),
    h("div", { class: "card kpi" }, h("div", { class: "label" }, icon("calendar"), "Subjects"), h("div", { class: "value" }, String(summary.classes.length))),
    h("div", { class: "card kpi" }, h("div", { class: "label" }, icon("info"), "How it is counted"), h("div", { class: "sub" }, "Present or partial (60–80% of the class) counts as attended. Classes where the camera could not measure are not counted for or against you."))));

  kids.push(summary.classes.length ? h("div", { class: "grid two" }, ...summary.classes.map(classCard)) : emptyState("No results yet", "Your attendance per class appears here after your first class."));
  kids.push(card("Recent classes", h("div", { id: "hist" }), { flush: true }));
  return h("div", { class: "stack" }, ...kids);
}

export function mount(host, ctx) {
  const state = { first: true, reg: null };
  api("/api/registration-info").then((r) => { state.reg = r; }).catch(() => {});
  const area = h("div", {});
  const enable = h("button", { class: "btn btn-sm", type: "button" }, icon("live"), "Enable desktop notifications");
  const canNotify = "Notification" in window && Notification.permission === "default";
  enable.addEventListener("click", async () => { try { const r = await Notification.requestPermission(); toast(r === "granted" ? "Notifications enabled." : "Notifications stay off.", "info"); enable.remove(); } catch { /* ignore */ } });
  host.append(h("div", { class: "toolbar" }, h("p", { class: "muted grow" }, `Hello ${ctx.user.name}. This page updates by itself.`), canNotify ? enable : null), area);
  const m = mountAsync(area, () => api("/api/me/student/overview"), (d) => {
    const node = render(d, state);
    api("/api/me/student/history?limit=40").then((hh) => {
      const t = node.querySelector("#hist"); if (!t) return;
      t.replaceChildren(hh.items.length ? table([{ label: "Date", key: "date" }, { label: "Class", key: "subject" }, { label: "Time", render: (r) => `${r.start_time || "—"}–${r.end_time || "—"}` },
        { label: "Present", render: (r) => mins(r.present_minutes) }, { label: "Attendance", render: (r) => pct(r.percentage) },
        { label: "Result", render: (r) => h("span", { class: "row" }, badge(r.status), r.is_override ? h("span", { class: "chip" }, "corrected") : null) }], hh.items, { caption: "Recent classes" }) : emptyState("No history yet", ""));
    }).catch((e) => { const t = node.querySelector("#hist"); t && t.replaceChildren(alertBox("err", null, humanMessage(e))); });
    return node;
  }, { poll: 60000 });
  return () => m.stop();
}
