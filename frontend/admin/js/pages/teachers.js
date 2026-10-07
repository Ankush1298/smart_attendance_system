import { api, humanMessage } from "../api.js";
import { editPerson, deletePerson, resetFace } from "./person.js";
import { h, icon, badge, card, table, mountAsync, emptyState, modal, busy, toast, field, fmtDateTime, alertBox } from "../ui.js";

export function mount(host, ctx) {
  const ro = !ctx.canEdit;
  const area = h("div", {});
  let m;
  host.append(h("p", { class: "page-lead" }, "Teachers authorize their own classes: the camera must recognise the scheduled teacher before student attendance starts. Teachers register their face through the registration portal."), area);
  const portal = `${location.protocol}//${location.hostname}:5050/teacher`;
  m = mountAsync(area, async () => ({ u: await api("/api/users?role=teacher"), tt: await api("/api/timetable"), f: await api("/api/teacher-flags") }), ({ u, tt, f }) => {
    const classes = (t) => tt.items.filter((x) => x.teacher_id === t.roll_no || (x.teacher_id || "").toLowerCase() === t.name.toLowerCase()).length;
    const flags = (t) => f.items.filter((x) => x.teacher_id === t.roll_no).length;
    const unreg = [...new Set(tt.items.map((x) => x.teacher_id))].filter((id) => !u.items.some((t) => t.roll_no === id || t.name.toLowerCase() === (id || "").toLowerCase()));
    return h("div", { class: "stack" },
      unreg.length ? alertBox("warn", "Timetable names teachers who have not registered", `${unreg.join(", ")} — their classes cannot start until they register.`) : null,
      card(`Registered teachers (${u.count})`, u.items.length ? table([
        { label: "ID", key: "roll_no", cls: "mono" }, { label: "Name", key: "name" }, { label: "Classes / week", render: classes },
        { label: "Absence flags", render: (t) => (flags(t) ? badge("partial_absent", `${flags(t)} flag(s)`) : h("span", { class: "muted" }, "none")) },
        { label: "Face", render: (t) => (t.face_registered ? badge("present", "Registered") : badge("warning", "Not registered")) },
        { label: "Registered", render: (t) => fmtDateTime(t.registered_at) },
        ...(ro ? [] : [{ label: "", cls: "actions", render: (t) => h("span", { class: "btn-group" },
          t.face_registered ? h("button", { class: "btn btn-sm", type: "button", "aria-label": `Reset face of ${t.name}`, on: { click: () => resetFace(t, () => m.reload()) } }, icon("camera"), "Reset face") : null,
            h("button", { class: "btn btn-sm", type: "button", "aria-label": `Edit ${t.name}`, on: { click: () => editPerson(t, () => m.reload()) } }, icon("edit"), "Edit"),
          h("button", { class: "btn btn-sm", type: "button", "aria-label": `Delete ${t.name}`, on: { click: () => deletePerson(t, () => m.reload()) } }, icon("x"), "Delete")) }]),
      ], u.items, { caption: "Teachers" }) : emptyState("No teachers registered", `Ask each teacher to open ${portal} on the school network and complete face registration.`), { flush: true }),
      card("How teachers register", h("div", { class: "card-body" }, h("p", {}, "Registration uses the secure portal started with the desktop application (", h("span", { class: "mono" }, portal), "). The administrator creates an ID and password for each teacher first; the teacher then captures their face from a phone or laptop."), h("p", { class: "muted" }, "Face images are never stored; only numeric embeddings are kept in MySQL and they are never shown in this interface.")), { flush: false }));
  });
  return () => m.stop();
}
