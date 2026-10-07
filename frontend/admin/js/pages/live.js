import { api } from "../api.js";
import { h, icon, badge, mountAsync, emptyState, fmtTime, relTime, parseTs } from "../ui.js";

function timeline(s) {
  const a = parseTs(s.start), b = parseTs(s.end), cs = parseTs(s.counted_start), ce = parseTs(s.counted_end), now = Date.now();
  const total = Math.max(1, b - a);
  const L = ((cs - a) / total) * 100, W = ((ce - cs) / total) * 100, E = Math.min(100, Math.max(0, ((now - a) / total) * 100));
  return h("div", {}, h("div", { class: "bar", role: "img", "aria-label": `Lecture ${fmtTime(s.start)} to ${fmtTime(s.end)}, counted ${fmtTime(s.counted_start)} to ${fmtTime(s.counted_end)}` },
    h("div", { class: "counted", vars: { "--l": L + "%", "--w": W + "%" } }), h("div", { class: "elapsed", vars: { "--w": E + "%" } })),
  h("div", { class: "bar-legend" }, h("span", {}, fmtTime(s.start)), h("span", {}, `counted ${fmtTime(s.counted_start)}–${fmtTime(s.counted_end)}`), h("span", {}, fmtTime(s.end))));
}

function liveCard(s) {
  const dd = (k, v) => h("div", {}, h("dt", {}, k), h("dd", {}, v));
  return h("article", { class: "card live-card", "aria-label": `${s.subject} in ${s.room_id}` },
    h("header", { class: "split" }, h("div", {}, h("h3", {}, s.subject), h("div", { class: "muted" }, `Room ${s.room_id} · ${s.teacher_id}`)), badge(s.state)),
    timeline(s),
    h("dl", { class: "meta" },
      dd("Teacher", s.teacher_authorized ? h("span", { class: "badge ok" }, icon("check"), "Verified") : h("span", { class: "badge info" }, icon("clock"), "Not yet seen")),
      dd("Teacher last seen", s.teacher_last_seen ? relTime(s.teacher_last_seen) : "—"),
      dd("Students seen", s.students_eligible === null ? `${s.students_seen} (no section)` : `${s.students_seen} of ${s.students_eligible}`),
      dd("Camera", s.camera_blind ? h("span", { class: "badge warn" }, icon("alert"), "Not delivering") : badge(s.camera_status || "UNKNOWN")),
      dd("Section", s.section_id || "none"),
      dd("Last scan", s.last_scan?.at ? relTime(s.last_scan.at) : "—")),
    s.teacher_absence_alert ? h("div", { class: "alert err", role: "alert" }, icon("alert"), h("div", { class: "alert-body" }, h("strong", {}, "Teacher absence flag"), "The scheduled teacher has not been recognised for over 20 continuous minutes. This is reported to the HOD/admin; the class is not marked absent.")) : null,
    s.camera_blind ? h("div", { class: "alert warn", role: "status" }, icon("alert"), h("div", { class: "alert-body" }, h("strong", {}, "Measurements paused"), s.last_scan?.unmeasurable_reason || "No camera is delivering frames.", " This time is recorded as unmeasurable, not as absence.")) : null,
    s.note ? h("div", { class: "pill-note" }, s.note) : null);
}

function render(d) {
  const running = d.items.filter((s) => !s.finished);
  const finished = d.items.filter((s) => s.finished && s.state !== "QUARANTINED");
  const quarantined = d.items.filter((s) => s.state === "QUARANTINED");
  if (!d.items.length) return emptyState("No class is running", "When a scheduled class starts, its live status appears here. Nothing needs to be started manually.", h("a", { class: "btn", href: "#/timetable" }, "View timetable"));
  return h("div", { class: "stack" },
    running.length ? h("div", { class: "grid two" }, ...running.map(liveCard)) : emptyState("No class is running right now", "Finished classes today are listed below."),
    quarantined.length ? h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Quarantined slots")),
      h("ul", { class: "timeline" }, ...quarantined.map((s) => h("li", {}, badge("QUARANTINED"), h("div", { class: "what" }, h("strong", {}, `${s.subject} · ${s.room_id}`), " — ", s.note))))) : null,
    finished.length ? h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Finished today")),
      h("ul", { class: "timeline" }, ...finished.map((s) => h("li", {}, badge(s.state), h("div", { class: "what" }, h("strong", {}, `${s.subject} · ${s.room_id}`), ` — ${fmtTime(s.start)}–${fmtTime(s.end)}`))))) : null);
}

export function mount(host) {
  const m = mountAsync(host, () => api("/api/live-sessions"), render, { poll: 5000 });
  return () => m.stop();
}
