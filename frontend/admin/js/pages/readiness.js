import { api } from "../api.js";
import { h, icon, badge, card, mountAsync, alertBox, busy } from "../ui.js";

const IC = { READY: "check", WARNING: "alert", ERROR: "x" };
const TONE = { READY: "ok", WARNING: "warn", ERROR: "err" };

export function mount(host, ctx) {
  let m;
  const re = h("button", { class: "btn", type: "button" }, icon("refresh"), "Re-check");
  re.addEventListener("click", busy(re, async () => { await m.reload(); ctx?.refreshStatus?.(); }));
  host.append(h("div", { class: "toolbar" }, h("p", { class: "muted grow" }, "Automatic attendance only runs while every required component is not in error. Each item says what is wrong and how to fix it."), re), h("div", { id: "rd" }));
  m = mountAsync(host.querySelector("#rd"), () => api("/api/readiness"), (d) => h("div", { class: "stack" },
    d.automatic_attendance_allowed
      ? alertBox(d.overall === "READY" ? "ok" : "warn", d.overall === "READY" ? "Everything is ready" : "Automatic attendance is running, but some items need attention", d.overall === "READY" ? "Classes will start by themselves at their scheduled time." : "Review the warnings below to avoid missed or partial attendance.")
      : alertBox("err", "Automatic attendance is paused", "Fix the items marked Error and required to resume."),
    card(null, h("div", {}, ...d.checks.map((c) => h("div", { class: "check" },
      h("div", { class: "ico " + TONE[c.status] }, icon(IC[c.status])),
      h("div", {}, h("h3", {}, c.label, c.mandatory ? h("span", { class: "chip" }, "required") : null), h("div", {}, c.detail), c.fix && c.status !== "READY" ? h("div", { class: "fix" }, h("strong", {}, "How to fix: "), c.fix) : null),
      badge(c.status)))), { flush: true })), { poll: 20000 });
  return () => m.stop();
}
