import { api, humanMessage } from "../api.js";
import { h, icon, card, mountAsync, field, busy, toast, alertBox } from "../ui.js";

const FIELDS = [
  ["start_margin_min", "Start margin (minutes)", "Time at the start of every lecture that is not counted, for students and teachers. Default 5."],
  ["end_margin_min", "End margin (minutes)", "Time at the end of every lecture that is not counted. Default 5."],
  ["heartbeat_sec", "Recognition interval (seconds)", "How often students are recognised while the class is active. Default 60."],
  ["waiting_poll_sec", "Teacher check interval (seconds)", "How often the camera looks for the teacher before the class is authorized. Default 15."],
  ["dwell_half_window_sec", "Presence per detection (± seconds)", "Each recognition counts as presence this long on either side. Default 30."],
  ["teacher_bridge_min", "Teacher gap tolerance (minutes)", "A teacher not recognised for less than this is treated as still in the room. Default 3."],
  ["teacher_absence_alert_min", "Teacher absence flag (minutes)", "Continuous unrecognised time that raises an HOD/admin flag. Default 20."],
  ["teacher_wait_limit_min", "Wait for teacher (minutes)", "If the scheduled teacher is not recognised by then (and the camera worked) the session is suspended. Default 20."],
  ["min_measurable_ratio", "Minimum measurable share (0–1)", "If the camera was blind for more than the rest of this share of the lecture, results are marked unmeasurable instead of absent. Default 0.5."],
  ["scan_frames", "Frames per recognition", "Short burst captured each interval; two confirming frames are required. Default 4."],
  ["scan_frame_delay_sec", "Delay between frames (seconds)", "Default 0.2."],
];

export function mount(host) {
  let m;
  m = mountAsync(host, () => api("/api/settings"), (d) => {
    const inputs = {};
    const form = h("form", { novalidate: true }, ...FIELDS.map(([k, label, hint]) => { inputs[k] = h("input", { type: "number", id: "s-" + k, value: String(d.settings[k]), step: "any", min: "0", required: true }); return field(label, inputs[k], hint); }));
    const status = h("div", { "aria-live": "polite" });
    const save = h("button", { class: "btn btn-primary", type: "submit" }, "Save settings");
    form.append(status, save);
    form.addEventListener("submit", busy(save, async (e) => {
      e.preventDefault(); status.replaceChildren();
      const body = {};
      for (const [k] of FIELDS) {
        const v = inputs[k].value.trim();
        inputs[k].removeAttribute("aria-invalid");
        if (v === "" || isNaN(Number(v)) || Number(v) < 0) { inputs[k].setAttribute("aria-invalid", "true"); status.replaceChildren(alertBox("err", null, "Enter a valid non-negative number in every field.")); inputs[k].focus(); return; }
        body[k] = Number(v);
      }
      try { await api("/api/settings", { method: "PUT", json: body }); toast("Settings saved. They apply from the next scheduler cycle.", "ok"); }
      catch (err) { status.replaceChildren(alertBox("err", "Not saved", humanMessage(err))); }
    }));
    return h("div", { class: "grid two" }, card("Attendance policy", h("div", { class: "card-body" }, form), { flush: false }),
      h("div", { class: "stack" }, card("About", h("div", { class: "card-body stat-list" },
        h("div", { class: "line" }, h("span", { class: "muted" }, "Version"), h("strong", {}, d.version)), h("div", { class: "line" }, h("span", { class: "muted" }, "Server time zone"), h("strong", {}, d.timezone)),
        h("div", { class: "line" }, h("span", { class: "muted" }, "Server time"), h("strong", {}, d.server_time))), { flush: false }),
      card("Configured in the server environment", h("div", { class: "card-body" }, h("p", {}, "Database credentials, the administrator password, the time zone and camera permissions are set in the server's ", h("span", { class: "mono" }, ".env"), " file and cannot be changed from the browser. Restart the server after editing it."),
        h("p", { class: "muted" }, "See the README, “Environment variables”.")), { flush: false })));
  });
  return () => m.stop();
}
