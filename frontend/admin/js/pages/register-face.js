// Face-registration instructions with a QR code (scan with a phone) for a student or teacher.
import { api } from "../api.js";
import { h, icon, card, alertBox, mountAsync } from "../ui.js";

export function registerCard(role, reg, { compact = false } = {}) {
  const url = role === "teacher" ? reg.teacher_url : reg.student_url;
  const label = role === "teacher" ? "Teacher" : "Student";
  if (!reg.enabled) return alertBox("warn", "Face registration is switched off", "Ask your administrator to enable the registration portal.");
  const img = h("img", { class: "qr", src: `/api/registration-qr?role=${role}&t=${Date.now() % 100000}`, alt: `QR code that opens the ${label.toLowerCase()} face-registration page`, width: "220", height: "220" });
  return h("div", { class: "qr-card" }, img, h("div", { class: "stack" },
    h("h3", {}, `${label} face registration`),
    h("ol", { class: "steps" }, h("li", {}, "Scan the QR code with your phone camera (or open the link below)."), h("li", {}, "Accept the one-time security warning (the school server uses its own certificate)."),
      h("li", {}, "Sign in with the ID and password your administrator gave you."), h("li", {}, "Follow the on-screen steps: look at the camera and slowly turn your head.")),
    h("a", { href: url, target: "_blank", rel: "noopener", class: "mono" }, url),
    compact ? null : h("p", { class: "muted small" }, "Your phone must be on the same Wi-Fi as the school server. Only numeric face data is stored, never photos.")));
}

export function mount(host, ctx) {
  const area = h("div", {});
  host.append(h("p", { class: "page-lead" }, "Register your face once so the classroom cameras can recognise you."), area);
  const m = mountAsync(area, () => api("/api/registration-info", { auth: false }), (reg) => card(null, h("div", { class: "card-body" }, registerCard(ctx.role === "teacher" ? "teacher" : "student", reg)), { flush: false }));
  return () => m.stop();
}
