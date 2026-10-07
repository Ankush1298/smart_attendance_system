// Manual correction dialog shared by admins (audited) and teachers (own classes, password re-confirmed).
import { api, humanMessage } from "../api.js";
import { h, modal, field, busy, toast, alertBox, clear } from "../ui.js";

export function overrideDialog({ sessionId, student, teacherMode, onDone }) {
  const status = h("select", { id: "ov-status" }, ...["present", "warning", "absent"].map((v) => h("option", { value: v, selected: v !== student.status }, v[0].toUpperCase() + v.slice(1))));
  const reason = h("input", { type: "text", id: "ov-reason", maxlength: "500", placeholder: "e.g. was at the lab, camera could not see them" });
  const pw = teacherMode ? h("input", { type: "password", id: "ov-pw", autocomplete: "current-password" }) : null;
  const out = h("div", { "aria-live": "polite" });
  const save = h("button", { class: "btn btn-primary", type: "button" }, "Save correction");
  const body = h("div", {}, h("p", { class: "muted" }, `${student.name} (${student.roll_no}) is currently ${student.status.toUpperCase()}. The change is audited with your name and reason.`),
    field("New status", status), field("Reason (required)", reason, "At least 3 characters."), pw ? field("Your password", pw, "Re-enter it to confirm it is you.") : null, out);
  const dlg = modal({ title: "Correct attendance", body, actions: [save] });
  save.addEventListener("click", busy(save, async () => {
    clear(out);
    if (reason.value.trim().length < 3) { out.append(alertBox("err", null, "Please give a reason (at least 3 characters).")); reason.focus(); return; }
    if (teacherMode && !pw.value) { out.append(alertBox("err", null, "Enter your password to confirm.")); pw.focus(); return; }
    try {
      const json = { session_id: sessionId, roll_no: student.roll_no, new_status: status.value, reason: reason.value.trim(), ...(teacherMode ? { password: pw.value } : {}) };
      await api(teacherMode ? "/api/me/teacher/override" : "/api/attendance/override", { method: "POST", json });
    } catch (e) { out.append(alertBox("err", "Not saved", humanMessage(e))); throw e; }
    dlg.close(); toast("Attendance corrected.", "ok"); onDone && onDone();
  }));
}
