import { api } from "../api.js";
import { overrideDialog } from "./override.js";
import { h, icon, badge, card, table, mountAsync, emptyState, modal, busy, toast, download, field, fmtTime, fmtTimeS, mins, pct, todayStr } from "../ui.js";

function sessionDetail(id, canEdit) {
  const body = h("div", {});
  const reloadDetail = () => mountAsync(body, () => api("/api/sessions/" + encodeURIComponent(id)), (d) => {
    const s = d.session, t = d.teacher;
    const progress = (v, status) => h("div", { class: "row" }, h("div", { class: "progress " + (status === "present" ? "" : status === "warning" ? "warn" : "err"), role: "img", "aria-label": pct(v) }, h("span", { vars: { "--w": Math.min(100, v || 0) + "%" } })), h("span", { class: "nowrap" }, pct(v)));
    return h("div", { class: "stack" },
      h("div", { class: "row" }, badge(s.state), h("span", { class: "muted" }, `${s.subject || ""} · Room ${s.room_id || "—"} · ${s.date} · counted ${fmtTime(s.counted_start)}–${fmtTime(s.counted_end)} (UTC→local)`)),
      t ? card("Teacher", h("div", { class: "card-body" }, h("div", { class: "row" }, h("strong", {}, s.teacher_id), badge(t.status), t.absence_over_20m ? badge("partial_absent", "Absence flag") : null),
        h("div", { class: "muted" }, `Present ${mins(t.present_minutes)} · away ${mins(t.absent_minutes)} · longest absence ${mins(t.longest_absence_minutes)}`)), { flush: true }) : null,
      d.unmeasurable.length ? card("Unmeasurable intervals (camera or engine unavailable)", h("ul", { class: "timeline" }, ...d.unmeasurable.map((u) => h("li", {}, h("span", { class: "when" }, `${fmtTimeS(u.started_at)}–${fmtTimeS(u.ended_at)}`), h("div", { class: "what" }, u.reason || "")))), { flush: true }) : null,
      card(`Students (${d.students.length})`, d.students.length ? table([
        { label: "Roll no", key: "roll_no", cls: "mono" }, { label: "Name", key: "name" },
        { label: "Present", render: (r) => mins(r.present_minutes) }, { label: "Measurable", render: (r) => mins(r.measurable_minutes) },
        { label: "Attendance", render: (r) => progress(r.percentage, r.status) }, { label: "Status", render: (r) => h("span", { class: "row" }, badge(r.status), r.is_override ? h("span", { class: "chip" }, "manual") : null) },
        ...(canEdit ? [{ label: "", cls: "actions", render: (r) => h("button", { class: "btn btn-sm", type: "button", on: { click: () => overrideDialog({ sessionId: id, student: r, teacherMode: false, onDone: reloadDetail }) } }, icon("edit"), "Correct") }] : []),
      ], d.students) : emptyState("No student results", "Students are scored when the class ends."), { flush: true }),
      card("Timeline", d.events.length ? h("ul", { class: "timeline" }, ...d.events.map((e) => h("li", {}, h("span", { class: "when" }, fmtTimeS(e.occurred_at)), h("div", { class: "what" }, e.event_type === "state" ? `${e.from_state} → ${e.to_state}${e.detail ? " — " + e.detail : ""}` : `${e.event_type.replaceAll("_", " ")} — ${e.detail || ""}`)))) : emptyState("No events", ""), { flush: true }));
  });
  reloadDetail();
  return body;
}

export function mount(host, ctx) {
  let date = todayStr();
  const dateIn = h("input", { type: "date", id: "att-date", value: date, "aria-label": "Date" });
  const csvBtn = h("button", { class: "btn", type: "button" }, icon("download"), "Export CSV");
  csvBtn.addEventListener("click", busy(csvBtn, () => download("/api/reports/attendance.csv?date=" + date, `attendance-${date}.csv`)));
  const out = h("div", {});
  const bar = h("div", { class: "toolbar" }, h("div", {}, h("label", { for: "att-date" }, "Date"), dateIn), h("div", { class: "grow" }), csvBtn);
  let m;
  const load = () => { m?.stop(); m = mountAsync(out, () => api("/api/sessions?date=" + date), (d) => d.items.length ? card(null, table([
    { label: "Time", render: (r) => `${r.start_time || "—"}–${r.end_time || "—"}` }, { label: "Subject", key: "subject" }, { label: "Room", key: "room_id" },
    { label: "Teacher", key: "teacher_name" }, { label: "Section", render: (r) => r.section_id || "—" },
    { label: "Students", render: (r) => h("span", { class: "row" }, h("span", { class: "badge ok" }, `${r.n_present} present`), r.n_warning ? h("span", { class: "badge warn" }, `${r.n_warning} warn`) : null, h("span", { class: "badge err" }, `${r.n_absent} absent`), r.n_unmeasurable ? h("span", { class: "badge" }, `${r.n_unmeasurable} n/a`) : null) },
    { label: "Status", render: (r) => badge(r.state || "COMPLETED") },
  ], d.items, { onRow: (r) => modal({ title: `${r.subject || "Session"} — ${r.room_id || ""}`, wide: true, body: sessionDetail(r.session_id, ctx.canEdit) }), caption: "Sessions" }), { flush: true })
    : emptyState("No sessions on this date", "Sessions are created automatically from the timetable when a class starts.")); };
  dateIn.addEventListener("change", () => { date = dateIn.value || todayStr(); load(); });
  host.append(h("p", { class: "page-lead" }, "Every class session and its final attendance. Select a row for the full breakdown, including camera outages and the event timeline."), bar, out);
  load();
  return () => m?.stop();
}
