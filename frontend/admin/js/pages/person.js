// Edit / delete a registered student or teacher (admins only; the server enforces it too).
import { api, humanMessage } from "../api.js";
import { h, modal, field, busy, toast, alertBox, confirmDialog, clear } from "../ui.js";

export function editPerson(p, onDone) {
  const name = h("input", { type: "text", id: "p-name", value: p.name, maxlength: "255", required: true });
  const id = h("input", { type: "text", id: "p-id", value: p.roll_no, maxlength: "100", required: true });
  const role = h("select", { id: "p-role" }, ...["student", "teacher"].map((r) => h("option", { value: r, selected: r === p.role }, r[0].toUpperCase() + r.slice(1))));
  const out = h("div", { "aria-live": "polite" });
  const save = h("button", { class: "btn btn-primary", type: "button" }, "Save changes");
  const dlg = modal({ title: `Edit ${p.name}`, body: h("div", {}, field("Full name", name), field("ID / roll number", id, "Changing the ID updates their login, attendance history, sections and timetable entries."),
    field("Role", role, "Switching to teacher removes them from student sections."), out), actions: [save] });
  save.addEventListener("click", busy(save, async () => {
    clear(out);
    if (!name.value.trim() || !id.value.trim()) { out.append(alertBox("err", null, "Name and ID cannot be empty.")); return; }
    const json = {};
    if (name.value.trim() !== p.name) json.name = name.value.trim();
    if (id.value.trim() !== p.roll_no) json.new_id = id.value.trim();
    if (role.value !== p.role) json.role = role.value;
    if (!Object.keys(json).length) { dlg.close(); return; }
    try { await api("/api/users/" + encodeURIComponent(p.roll_no), { method: "PATCH", json }); }
    catch (e) { out.append(alertBox("err", "Not saved", humanMessage(e))); throw e; }
    dlg.close(); toast("Saved.", "ok"); onDone();
  }));
}

export async function deletePerson(p, onDone) {
  const ok = await confirmDialog({ title: `Delete ${p.name}?`, confirmLabel: "Delete permanently", danger: true,
    message: `This permanently removes ${p.name} (${p.roll_no}): their face registration, their login, their section memberships and ALL of their attendance history. This cannot be undone. Registered people who simply left can instead be kept; only delete if you are sure.` });
  if (!ok) return;
  try {
    const r = await api(`/api/users/${encodeURIComponent(p.roll_no)}?confirm=true`, { method: "DELETE" });
    toast(r.timetable_slots_still_naming_them ? `Deleted. ${r.timetable_slots_still_naming_them} timetable slot(s) still name this teacher; update the timetable.` : "Deleted.", r.timetable_slots_still_naming_them ? "warn" : "ok", 9000);
    onDone();
  } catch (e) { toast(humanMessage(e), "err"); }
}

export async function resetFace(p, onDone) {
  const ok = await confirmDialog({ title: `Reset ${p.name}'s face?`, confirmLabel: "Delete face data", danger: true,
    message: `This deletes only the stored face of ${p.name} (${p.roll_no}). Their login, section and attendance history stay. Until they register again (QR code on Register My Face / the sign-in page) the cameras cannot recognise them, so they will not be marked present.` });
  if (!ok) return;
  try { await api(`/api/users/${encodeURIComponent(p.roll_no)}/face`, { method: "DELETE" }); toast("Face deleted. They can now register again.", "ok", 7000); onDone(); }
  catch (e) { toast(humanMessage(e), "err"); }
}
