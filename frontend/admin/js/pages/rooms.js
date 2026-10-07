import { api, humanMessage } from "../api.js";
import { h, icon, badge, card, table, mountAsync, emptyState, modal, busy, toast, field, confirmDialog } from "../ui.js";

export function mount(host) {
  const area = h("div", {});
  let m;
  function edit(room) {
    const id = h("input", { type: "text", id: "r-id", value: room?.room_id || "", maxlength: "100", disabled: !!room, required: true, placeholder: "e.g. R101" });
    const name = h("input", { type: "text", id: "r-name", value: room?.room_name || "", maxlength: "255", required: true, placeholder: "e.g. Room 101" });
    const save = h("button", { class: "btn btn-primary", type: "button" }, room ? "Save" : "Add room");
    const dlg = modal({ title: room ? `Edit ${room.room_id}` : "Add room", body: h("div", {}, field("Room ID", id, room ? "The ID cannot be changed because the timetable refers to it." : "Must match the room used in the timetable."), field("Name", name)), actions: [save] });
    save.addEventListener("click", busy(save, async () => {
      if (!id.value.trim() || !name.value.trim()) { toast("Enter a room ID and a name.", "warn"); return; }
      if (room) await api("/api/rooms/" + encodeURIComponent(room.room_id), { method: "PUT", json: { room_name: name.value.trim() } });
      else await api("/api/rooms", { method: "POST", json: { room_id: id.value.trim(), room_name: name.value.trim() } });
      dlg.close(); toast("Saved.", "ok"); m.reload();
    }));
  }
  const addBtn = () => h("button", { class: "btn btn-primary", type: "button", on: { click: () => edit(null) } }, icon("plus"), "Add room");
  const add = addBtn();
  host.append(h("div", { class: "toolbar" }, h("p", { class: "muted grow" }, "Each room used by the timetable needs at least one enabled camera. Manage cameras on the Cameras page."), add), area);
  m = mountAsync(area, () => api("/api/rooms"), (d) => d.items.length ? card(null, table([
    { label: "Room ID", key: "room_id", cls: "mono" }, { label: "Name", key: "room_name" },
    { label: "Cameras", render: (r) => (r.cameras ? h("span", { class: "row" }, `${r.cameras}`, r.cameras_online ? badge("ONLINE", `${r.cameras_online} online`) : badge("UNKNOWN", "none online")) : badge("ERROR", "No camera")) },
    { label: "Timetable slots", key: "slots" },
    { label: "", cls: "actions", render: (r) => h("span", {}, h("button", { class: "btn btn-sm", type: "button", on: { click: () => edit(r) } }, icon("edit"), "Edit"),
      h("button", { class: "btn btn-sm", type: "button", "aria-label": `Delete room ${r.room_id}`, on: { click: async () => {
        if (!(await confirmDialog({ title: "Delete room?", message: `Delete ${r.room_id}? Cameras assigned to it stay but become unassigned.`, confirmLabel: "Delete", danger: true }))) return;
        try { await api("/api/rooms/" + encodeURIComponent(r.room_id), { method: "DELETE" }); toast("Room deleted.", "ok"); m.reload(); } catch (e) { toast(humanMessage(e), "err"); } } } }, "Delete")) },
  ], d.items, { caption: "Rooms" }), { flush: true }) : emptyState("No rooms yet", "Rooms are created when you publish a timetable, or add one manually.", addBtn()));
  return () => m.stop();
}
