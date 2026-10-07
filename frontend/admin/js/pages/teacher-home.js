import { api, humanMessage } from "../api.js";
import { h, icon, badge, card, table, mountAsync, emptyState, alertBox, modal, fmtTime, fmtTimeS, fmtDateTime, mins, pct, cap } from "../ui.js";
import { overrideDialog } from "./override.js";
import { registerCard } from "./register-face.js";

const DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];

function sessionModal(row, reload) {
  const body = h("div", {});
  const load = () => mountAsync(body, () => api("/api/me/teacher/sessions/" + encodeURIComponent(row.session_id)), (d) => h("div", { class: "stack" },
    d.teacher ? h("div", { class: "row" }, badge(d.teacher.status), h("span", { class: "muted" }, `You were present ${mins(d.teacher.present_minutes)}, away ${mins(d.teacher.absent_minutes)} (counted window only).`)) : null,
    d.unmeasurable.length ? alertBox("info", "Camera/engine outage", `${d.unmeasurable.length} interval(s) could not be measured; they are not counted against anyone.`) : null,
    card(`Students (${d.students.length})`, d.students.length ? table([
      { label: "Roll no", key: "roll_no", cls: "mono" }, { label: "Name", key: "name" }, { label: "Present", render: (r) => mins(r.present_minutes) }, { label: "Attendance", render: (r) => pct(r.percentage) },
      { label: "Result", render: (r) => h("span", { class: "row" }, badge(r.status), r.is_override ? h("span", { class: "chip" }, "corrected") : null) },
      { label: "", cls: "actions", render: (r) => h("button", { class: "btn btn-sm", type: "button", on: { click: () => overrideDialog({ sessionId: row.session_id, student: r, teacherMode: true, onDone: () => { load(); reload && reload(); } }) } }, icon("edit"), "Correct") },
    ], d.students) : emptyState("No results yet", "Results appear when the class ends."), { flush: true })));
  load();
  modal({ title: `${row.subject || "Class"} — ${row.date}`, wide: true, body });
}

function render(d, ctx, reload) {
  const live = d.live.filter((x) => !x.finished);
  const stat = d.my_attendance || {};
  return h("div", { class: "stack" },
    !d.profile.face_registered ? h("div", { class: "stack" }, alertBox("warn", "Your face is not registered yet", "The camera cannot verify you, so your classes cannot start automatically. Scan the code below with your phone, or use Register My Face in the menu."), ctx.reg ? card(null, h("div", { class: "card-body" }, registerCard("teacher", ctx.reg)), { flush: false }) : null) : null,
    live.length ? h("div", { class: "grid two" }, ...live.map((s) => h("article", { class: "card live-card" }, h("header", { class: "split" }, h("div", {}, h("h3", {}, s.subject), h("div", { class: "muted" }, `Room ${s.room_id}`)), badge(s.state)),
      h("dl", { class: "meta" }, h("div", {}, h("dt", {}, "You"), h("dd", {}, s.teacher_authorized ? "Verified" : "Not yet seen by the camera")), h("div", {}, h("dt", {}, "Students seen"), h("dd", {}, s.students_eligible === null ? String(s.students_seen) : `${s.students_seen} of ${s.students_eligible}`))),
      s.state === "WAITING_TEACHER" ? h("div", { class: "pill-note" }, "Stand where the camera can see your face for a moment to start the class.") : null))) : null,
    card("Today's classes", d.today.length ? table([{ label: "Time", render: (r) => `${r.start_time}–${r.end_time}` }, { label: "Subject", key: "subject" }, { label: "Room", key: "room_id" }, { label: "Section", render: (r) => r.section_id || "—" }], d.today) : emptyState("No classes today", "Nothing is scheduled for you today."), { flush: true }),
    h("div", { class: "grid kpis" },
      h("div", { class: "card kpi" }, h("div", { class: "label" }, icon("teacher"), "My attendance"), h("div", { class: "value" }, String(Object.values(stat).reduce((a, b) => a + b, 0))), h("div", { class: "sub" }, `present ${stat.present || 0} · long absence ${stat.partial_absent || 0} · absent ${stat.absent || 0}`)),
      h("div", { class: "card kpi" }, h("div", { class: "label" }, icon("alert"), "Absence flags"), h("div", { class: "value" }, String(d.flags.length)), h("div", { class: "sub" }, "20+ continuous minutes unrecognised"))),
    card("My recent classes", d.sessions.length ? table([{ label: "Date", key: "date" }, { label: "Time", render: (r) => `${r.start_time || "—"}–${r.end_time || "—"}` }, { label: "Subject", key: "subject" }, { label: "Room", key: "room_id" },
      { label: "Students", render: (r) => h("span", { class: "row" }, h("span", { class: "badge ok" }, `${r.n_present} present`), r.n_warning ? h("span", { class: "badge warn" }, `${r.n_warning} partial`) : null, h("span", { class: "badge err" }, `${r.n_absent} absent`), r.n_unmeasurable ? h("span", { class: "badge" }, `${r.n_unmeasurable} n/a`) : null) },
      { label: "Status", render: (r) => badge(r.state || "COMPLETED") }], d.sessions, { onRow: (r) => sessionModal(r, reload), caption: "My classes" }) : emptyState("No classes held yet", "Held classes appear here with each student's result."), { flush: true }),
    card("My weekly timetable", d.week.length ? table([{ label: "Day", render: (r) => cap(r.day_of_week) }, { label: "Time", render: (r) => `${r.start_time}–${r.end_time}` }, { label: "Subject", key: "subject" }, { label: "Room", key: "room_id" }, { label: "Section", render: (r) => r.section_id || "—" }], d.week) : emptyState("No classes assigned", "Your classes appear once the timetable names you."), { flush: true }));
}

export function mount(host, ctx) {
  const area = h("div", {});
  const ctx2 = { ...ctx, reg: null };
  api("/api/registration-info").then((r) => { ctx2.reg = r; }).catch(() => {});
  host.append(h("p", { class: "page-lead" }, "Your classes start by themselves: when the camera recognises you in the room, attendance begins. Select a past class to see each student's result and correct a mistake (your password is asked again; every correction is recorded)."), area);
  let m; m = mountAsync(area, () => api("/api/me/teacher/overview"), (d) => render(d, ctx2, () => m.reload()), { poll: 15000 });
  return () => m.stop();
}
