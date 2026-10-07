import { api, humanMessage } from "../api.js";
import { editPerson, deletePerson, resetFace } from "./person.js";
import { h, icon, badge, card, table, mountAsync, emptyState, modal, busy, toast, field, confirmDialog, alertBox, fmtDateTime } from "../ui.js";

export function mount(host, ctx) {
  const ro = !ctx.canEdit;
  const area = h("div", {});
  let m, selected = new Set(), filter = "";
  host.append(h("p", { class: "page-lead" }, "A student receives attendance only for classes whose section they belong to. Create sections, then assign registered students to them."), area);

  function addSection() {
    const id = h("input", { type: "text", id: "sec-id", maxlength: "100", placeholder: "e.g. CSE-A", required: true });
    const name = h("input", { type: "text", id: "sec-name", maxlength: "255", placeholder: "e.g. CSE Year 2 – A", required: true });
    const save = h("button", { class: "btn btn-primary", type: "button" }, "Create section");
    const dlg = modal({ title: "New section", body: h("div", {}, field("Section ID", id, "Letters, numbers, spaces, - and _"), field("Display name", name)), actions: [save] });
    save.addEventListener("click", busy(save, async () => {
      if (!id.value.trim() || !name.value.trim()) { toast("Enter both an ID and a name.", "warn"); return; }
      await api("/api/sections", { method: "POST", json: { section_id: id.value.trim(), section_name: name.value.trim() } }); dlg.close(); toast("Section created.", "ok"); m.reload();
    }));
  }

  m = mountAsync(area, async () => ({ u: await api("/api/users?role=student"), s: await api("/api/sections") }), ({ u, s }) => {
    const rows = u.items.filter((x) => !filter || (x.roll_no + " " + x.name).toLowerCase().includes(filter));
    const unassigned = u.items.filter((x) => !x.sections).length;
    const sel = h("select", { id: "assign-sec", "aria-label": "Section to assign" }, h("option", { value: "" }, "Assign selected to…"), ...s.items.map((x) => h("option", { value: x.section_id }, x.section_name)));
    const assign = h("button", { class: "btn btn-primary", type: "button", disabled: !selected.size }, "Assign");
    assign.addEventListener("click", busy(assign, async () => {
      if (!sel.value) { toast("Choose a section first.", "warn"); return; }
      const cur = await api(`/api/sections/${encodeURIComponent(sel.value)}/students`);
      const roll_nos = [...new Set([...cur.items, ...selected])];
      const r = await api(`/api/sections/${encodeURIComponent(sel.value)}/students`, { method: "PUT", json: { roll_nos } });
      toast(`${selected.size} student(s) added. Section now has ${r.assigned}.`, "ok"); selected = new Set(); m.reload();
    }));
    const search = h("input", { type: "search", placeholder: "Search by name or roll number", "aria-label": "Search students", value: filter });
    search.addEventListener("input", () => { filter = search.value.trim().toLowerCase(); m.reload(); });
    const check = (r) => { const c = h("input", { type: "checkbox", "aria-label": `Select ${r.name}`, checked: selected.has(r.roll_no) }); c.addEventListener("change", () => { c.checked ? selected.add(r.roll_no) : selected.delete(r.roll_no); assign.disabled = !selected.size; }); return c; };
    return h("div", { class: "grid two" },
      h("div", { class: "stack" },
        unassigned ? alertBox("warn", `${unassigned} student(s) are not in any section`, "They will not receive attendance until assigned.") : null,
        h("div", { class: "toolbar" }, search, h("div", { class: "grow" }), ro ? null : sel, ro ? null : assign),
        card(`Registered students (${u.count})`, rows.length ? table([...(ro ? [] : [{ label: "", render: check }]), { label: "Roll no", key: "roll_no", cls: "mono" }, { label: "Name", key: "name" },
          { label: "Sections", render: (r) => (r.sections ? h("span", { class: "chips" }, ...r.sections.split(",").map((x) => h("span", { class: "chip" }, x))) : h("span", { class: "muted" }, "none")) },
          { label: "Face", render: (r) => (r.face_registered ? badge("present", "Registered") : badge("warning", "Not registered")) },
          { label: "Registered", render: (r) => fmtDateTime(r.registered_at) },
          ...(ro ? [] : [{ label: "", cls: "actions", render: (r) => h("span", { class: "btn-group" },
            r.face_registered ? h("button", { class: "btn btn-sm", type: "button", "aria-label": `Reset face of ${r.name}`, on: { click: () => resetFace(r, () => m.reload()) } }, icon("camera"), "Reset face") : null,
            h("button", { class: "btn btn-sm", type: "button", "aria-label": `Edit ${r.name}`, on: { click: () => editPerson(r, () => m.reload()) } }, icon("edit"), "Edit"),
            h("button", { class: "btn btn-sm", type: "button", "aria-label": `Delete ${r.name}`, on: { click: () => deletePerson(r, () => m.reload()) } }, icon("x"), "Delete")) }])], rows, { caption: "Students" })
          : emptyState(u.count ? "No match" : "No students registered", u.count ? "Try a different search." : "Students register their face through the registration portal."), { flush: true })),
      card("Sections", s.items.length ? table([{ label: "ID", key: "section_id", cls: "mono" }, { label: "Name", key: "section_name" }, { label: "Students", key: "students" }, { label: "Slots", key: "timetable_slots" },
        ...(ro ? [] : [{ label: "", cls: "actions", render: (x) => h("button", { class: "btn btn-sm", type: "button", "aria-label": `Delete section ${x.section_name}`, on: { click: async () => {
          if (!(await confirmDialog({ title: "Delete section?", message: `Delete "${x.section_name}"? Students stay registered; they simply lose this section.`, confirmLabel: "Delete", danger: true }))) return;
          try { await api("/api/sections/" + encodeURIComponent(x.section_id), { method: "DELETE" }); toast("Section deleted.", "ok"); m.reload(); } catch (e) { toast(humanMessage(e), "err"); } } } }, "Delete") }]) ], s.items)
        : emptyState("No sections", "Create a section such as “CSE-A”, then assign students and timetable slots to it."),
        { flush: true, actions: ro ? null : h("button", { class: "btn btn-sm btn-primary", type: "button", on: { click: addSection } }, icon("plus"), "New section") }));
  });
  return () => m.stop();
}
