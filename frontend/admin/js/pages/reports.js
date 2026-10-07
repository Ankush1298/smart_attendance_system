import { api } from "../api.js";
import { h, icon, badge, card, table, mountAsync, emptyState, busy, download, fmtDateTime, mins, todayStr } from "../ui.js";

export function mount(host) {
  let tab = "teachers", date = todayStr(), m;
  const area = h("div", {});
  const seg = h("div", { class: "seg", role: "group", "aria-label": "Report" });
  const dateIn = h("input", { type: "date", value: date, "aria-label": "Date" });
  const csv = h("button", { class: "btn", type: "button" }, icon("download"), "Export CSV");
  const bar = h("div", { class: "toolbar" }, seg, dateIn, h("div", { class: "grow" }), csv);
  host.append(bar, area);

  function draw() {
    seg.replaceChildren(...[["teachers", "Teacher attendance"], ["students", "Student attendance"], ["flags", "Absence flags"]].map(([id, label]) =>
      h("button", { type: "button", "aria-pressed": String(tab === id), on: { click: () => { tab = id; draw(); } } }, label)));
    dateIn.hidden = tab !== "students"; csv.hidden = tab === "flags";
    m?.stop();
    if (tab === "teachers") m = mountAsync(area, () => api("/api/teacher-attendance?limit=500"), (d) => d.items.length ? card(null, table([
      { label: "Date", key: "date" }, { label: "Subject", key: "subject" }, { label: "Room", key: "room" }, { label: "Teacher", key: "teacher_name" },
      { label: "Present", render: (r) => mins(r.present_minutes) }, { label: "Away", render: (r) => mins(r.absent_minutes) }, { label: "Longest absence", render: (r) => mins(r.longest_absence_minutes) },
      { label: "Status", render: (r) => h("span", { class: "row" }, badge(r.status), r.absence_over_20m ? badge("WARNING", "Flag") : null) }], d.items, { caption: "Teacher attendance" }), { flush: true })
      : emptyState("No teacher attendance yet", "Results appear when the first class finishes."));
    else if (tab === "students") m = mountAsync(area, () => api("/api/attendance?date=" + date + "&limit=2000"), (d) => d.items.length ? card(null, table([
      { label: "Subject", render: (r) => r.subject || "—" }, { label: "Room", render: (r) => r.room_id || "—" }, { label: "Roll no", key: "roll_no", cls: "mono" }, { label: "Name", key: "name" }, { label: "Role", key: "role" },
      { label: "Status", render: (r) => h("span", { class: "row" }, badge(r.status), r.is_override ? h("span", { class: "chip" }, "manual") : null) }], d.items, { caption: "Attendance" }), { flush: true })
      : emptyState("No attendance records", "Nothing was recorded on this date."));
    else m = mountAsync(area, () => api("/api/teacher-flags"), (d) => d.items.length ? card(null, table([
      { label: "Date", key: "date" }, { label: "Teacher", key: "teacher_name" }, { label: "Subject", key: "subject" }, { label: "Room", key: "room_id" },
      { label: "From", render: (r) => fmtDateTime(r.started_at) }, { label: "Continuous absence", render: (r) => mins(r.minutes) }], d.items, { caption: "Absence flags" }), { flush: true })
      : emptyState("No flags", "A flag is raised when a scheduled teacher is unrecognised for more than 20 continuous minutes."));
  }
  dateIn.addEventListener("change", () => { date = dateIn.value || todayStr(); draw(); });
  csv.addEventListener("click", busy(csv, () => tab === "teachers" ? download("/api/reports/teacher.csv", "teacher-attendance.csv") : download("/api/reports/attendance.csv?date=" + date, `attendance-${date}.csv`)));
  draw();
  return () => m?.stop();
}
