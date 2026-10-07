import { api } from "../api.js";
import { h, icon, badge, card, table, mountAsync, emptyState, alertBox, fmtTime, relTime, pct } from "../ui.js";

function kpi(label, ic, value, sub) {
  return h("div", { class: "card kpi" }, h("div", { class: "label" }, icon(ic), label), h("div", { class: "value" }, value), h("div", { class: "sub" }, ...sub));
}
const sub = (label, n) => h("span", {}, label + " ", h("b", {}, String(n ?? 0)));

function render(d) {
  const ta = d.teacher_attendance || {}, sa = d.student_attendance || {};
  const sTotal = (sa.present || 0) + (sa.warning || 0) + (sa.absent || 0);
  const tTotal = Object.values(ta).reduce((a, b) => a + b, 0);
  const live = d.classes.filter((c) => c.phase === "live").length;
  const children = [];
  if (!d.system.automatic_attendance_allowed) children.push(alertBox("err", "Automatic attendance is paused", "A required component is not ready. Classes will not start until it is fixed.", " ", h("a", { href: "#/readiness" }, "Open System Readiness")));

  children.push(h("div", { class: "grid kpis" },
    kpi("Today's classes", "calendar", String(d.counts.classes_today), [sub("Live", live), sub("Upcoming", d.counts.upcoming)]),
    kpi("Active sessions", "live", String(d.counts.active_sessions), [h("span", {}, "Running right now")]),
    kpi("Teacher attendance", "teacher", tTotal ? String(tTotal) : "—", [sub("Present", ta.present), sub("Long absence", ta.partial_absent), sub("Absent", ta.absent), sub("Unmeasurable", ta.unmeasurable)]),
    kpi("Student attendance", "student", sTotal ? pct(Math.round(((sa.present || 0) / sTotal) * 100)) : "—", [sub("Present", sa.present), sub("Warning", sa.warning), sub("Absent", sa.absent), sub("Unmeasurable", sa.unmeasurable)]),
    kpi("Cameras", "camera", `${d.cameras.online}/${d.cameras.enabled}`, [sub("Offline", d.cameras.offline), sub("Not used yet", d.cameras.unknown)]),
    kpi("Rooms", "room", `${d.rooms.online}/${d.rooms.total}`, [sub("Online", d.rooms.online), sub("Offline", d.rooms.offline)]),
  ));

  const classesTable = d.classes.length
    ? table([
      { label: "Time", render: (c) => h("span", { class: "nowrap" }, `${c.start_time}–${c.end_time}`) },
      { label: "Subject", key: "subject" }, { label: "Room", key: "room_id" }, { label: "Teacher", key: "teacher_name" },
      { label: "Section", render: (c) => c.section_id || h("span", { class: "muted" }, "none") },
      { label: "Status", render: (c) => (c.state ? badge(c.state) : badge(c.phase)) },
    ], d.classes, { caption: "Today's classes" })
    : emptyState("No classes today", "Nothing is scheduled for today. Publish a timetable on the Timetable page.");

  const warnings = d.warnings.length
    ? h("ul", { class: "timeline" }, ...d.warnings.map((w) => h("li", {}, h("span", {}, badge(w.level)), h("div", { class: "what" }, h("strong", {}, w.title + ": "), w.message, w.fix ? h("div", { class: "muted" }, w.fix) : null))))
    : emptyState("All clear", "No warnings or errors right now.");

  const events = d.recent_events.length
    ? h("ul", { class: "timeline" }, ...d.recent_events.map((e) => h("li", {}, h("span", { class: "when", title: e.occurred_at }, fmtTime(e.occurred_at)),
      h("div", { class: "what" }, h("strong", {}, `${e.subject || "Session"} · ${e.room_id || ""}`), " — ", e.event_type === "state" ? `${e.from_state} → ${e.to_state}${e.detail ? " (" + e.detail + ")" : ""}` : `${e.event_type.replaceAll("_", " ")}: ${e.detail || ""}`))))
    : emptyState("No activity yet", "Session events will appear here once a class starts.");

  children.push(card("Today's classes", classesTable, { actions: h("a", { class: "btn btn-sm", href: "#/live" }, "Live sessions") }));
  children.push(h("div", { class: "grid two" },
    card("Warnings and errors", warnings, { actions: h("a", { class: "btn btn-sm", href: "#/readiness" }, "Readiness") }),
    card("Recent activity", events)));
  return h("div", { class: "stack" }, ...children);
}

export function mount(host) {
  const m = mountAsync(host, () => api("/api/dashboard"), render, { poll: 15000 });
  return () => m.stop();
}
