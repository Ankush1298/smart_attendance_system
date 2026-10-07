import { api, ApiError, humanMessage } from "../api.js";
import { h, icon, badge, card, table, mountAsync, emptyState, modal, busy, toast, field, confirmDialog, alertBox, relTime, clear } from "../ui.js";

const SOURCE_HELP = {
  usb: ["Camera index", "0 is usually the first camera, 1 the second, and so on. Use “Scan available cameras” if unsure."],
  builtin: ["Camera index", "The laptop's built-in camera is usually 0. An external USB camera is usually 1 or higher."],
  droidcam: ["Phone address", "Open DroidCam on the phone and enter the address it shows, e.g. 192.168.1.20:4747. Phone and computer must be on the same Wi-Fi."],
  ip: ["Stream URL", "HTTP/MJPEG address, e.g. http://192.168.1.20:8080/video"],
  rtsp: ["RTSP URL", "e.g. rtsp://user:password@192.168.1.30:554/stream1 — see the camera's manual for the exact path."],
  file: ["Video file path", "Testing only: a video file on the server. Requires CAMERA_ALLOW_FILE_SOURCES=1."],
  none: ["", "This camera is kept for reference but is not used."],
};

function testResult(r) {
  if (r.ok) return alertBox("ok", "Camera works", `Received ${r.width}×${r.height} video${r.latency_ms ? ` in ${r.latency_ms} ms` : ""}.${r.note ? " " + r.note : ""}`);
  return alertBox("err", "Camera test failed", r.error || "The camera did not respond.");
}

function cameraForm({ cam, rooms, types, presetSource, onSaved }) {
  const full = cam ? { ...cam } : { name: "", camera_type: presetSource !== undefined ? "usb" : "usb", source: presetSource ?? "0", room_id: "", enabled: 1 };
  const name = h("input", { type: "text", id: "c-name", value: full.name, maxlength: "255", required: true, placeholder: "e.g. Camera R101" });
  const type = h("select", { id: "c-type" }, ...Object.entries(types).map(([k, v]) => h("option", { value: k, selected: k === full.camera_type }, v)));
  const source = h("input", { type: "text", id: "c-source", value: full.source, maxlength: "1000", autocomplete: "off" });
  const room = h("select", { id: "c-room" }, h("option", { value: "" }, "— not assigned —"), ...rooms.map((r) => h("option", { value: r.room_id, selected: r.room_id === full.room_id }, `${r.room_id} — ${r.room_name}`)));
  const enabled = h("input", { type: "checkbox", id: "c-enabled", checked: !!full.enabled });
  const srcWrap = h("div", {}), result = h("div", { "aria-live": "polite" });
  function drawSource() {
    const [label, hint] = SOURCE_HELP[type.value] || ["Source", ""];
    clear(srcWrap);
    if (type.value === "none") { srcWrap.append(h("p", { class: "muted" }, hint)); return; }
    source.setAttribute("inputmode", type.value === "usb" || type.value === "builtin" ? "numeric" : "url");
    srcWrap.append(field(label, source, hint));
  }
  type.addEventListener("change", () => { drawSource(); clear(result); });
  drawSource();
  const body = h("div", {}, field("Camera name", name), field("Camera type", type), srcWrap,
    field("Room", room, "The classroom this camera watches. A class only uses cameras assigned to its room."),
    h("div", { class: "field" }, h("label", { class: "switch", for: "c-enabled" }, enabled, "Enabled")), result);
  const test = h("button", { class: "btn", type: "button" }, icon("play"), "Test camera");
  const save = h("button", { class: "btn btn-primary", type: "button" }, cam ? "Save changes" : "Save camera");
  const payload = () => ({ name: name.value.trim(), camera_type: type.value, source: type.value === "none" ? "" : source.value.trim(), room_id: room.value || null, enabled: enabled.checked });
  test.addEventListener("click", busy(test, async () => {
    clear(result).append(h("div", { class: "row" }, h("span", { class: "spinner" }), "Opening the camera… (up to 15 s)"));
    try { const r = await api("/api/cameras/test", { method: "POST", json: { camera_type: type.value, source: source.value.trim() }, timeout: 40000 }); clear(result).append(testResult(r)); }
    catch (e) { clear(result).append(alertBox("err", "Test could not run", humanMessage(e))); }
  }));
  let dlg;
  save.addEventListener("click", busy(save, async () => {
    const p = payload();
    if (!p.name) { toast("Give the camera a name.", "warn"); name.focus(); return; }
    try {
      if (cam) await api("/api/cameras/" + cam.camera_id, { method: "PUT", json: p }); else await api("/api/cameras", { method: "POST", json: p });
    } catch (e) { clear(result).append(alertBox("err", "Could not save", humanMessage(e))); throw new ApiError("", { kind: "cancelled" }); }
    dlg.close(); toast("Camera saved.", "ok"); onSaved();
  }));
  dlg = modal({ title: cam ? `Edit ${cam.name}` : "Add camera", body, actions: [test, save] });
}

function preview(cam) {
  const img = h("img", { class: "preview-img", alt: `Live preview of ${cam.name}` }), msg = h("div", { class: "muted", role: "status" }, "Connecting…");
  const summary = h("div", { class: "stack", "aria-live": "polite" });
  let alive = true, url = null, failures = 0, detect = false;
  const toggle = h("button", { class: "btn btn-primary", type: "button", "aria-pressed": "false" }, icon("eye"), "Count faces");
  toggle.addEventListener("click", () => { detect = !detect; toggle.setAttribute("aria-pressed", String(detect)); toggle.lastChild.textContent = detect ? "Stop counting" : "Count faces"; clear(summary); });
  async function tick() {
    if (!alive) return;
    try {
      if (detect) {
        const d = await api(`/api/cameras/${cam.camera_id}/analyze`, { timeout: 45000 });
        if (!alive) return;
        if (d.image) img.src = d.image;
        clear(summary).append(h("div", { class: "row" }, badge(d.faces ? "present" : "unmeasurable", `${d.faces} face${d.faces === 1 ? "" : "s"} detected`), badge("present", `${d.recognized.length} recognised`), d.unknown ? badge("warning", `${d.unknown} unknown`) : null),
          d.recognized.length ? h("div", { class: "chips" }, ...d.recognized.map((p) => h("span", { class: "chip" }, `${p.name} · ${p.role} · ${Math.round(p.confidence * 100)}%`))) : h("div", { class: "muted small" }, d.faces ? "Faces are visible but nobody is recognised. Check lighting/angle, or that those people have registered." : "No face in view. Ask someone to stand in front of the camera."));
        msg.textContent = "Face test · green = recognised, orange = unknown · nothing is recorded";
      } else {
        const blob = await api(`/api/cameras/${cam.camera_id}/snapshot`, { blob: true, timeout: 20000 });
        if (!alive) return;
        if (url) URL.revokeObjectURL(url);
        url = URL.createObjectURL(blob); img.src = url; msg.textContent = "Live preview · refreshes every 2 s · no attendance is recorded";
      }
      failures = 0;
    } catch (e) { failures++; msg.textContent = humanMessage(e) + (failures > 2 ? " Close this window and press Test for details." : ""); }
    if (alive) setTimeout(tick, failures ? 4000 : detect ? 1500 : 2000);
  }
  modal({ title: `Preview — ${cam.name}`, wide: true, body: h("div", { class: "stack" }, img, msg, h("div", { class: "row" }, toggle, h("span", { class: "muted small" }, "Counts and names the people the camera can see right now.")), summary), onClose: () => { alive = false; if (url) URL.revokeObjectURL(url); } });
  tick();
}

function scanDialog(onUse) {
  const out = h("div", {});
  const dlg = modal({ title: "Scan available cameras", body: out });
  clear(out).append(h("div", { class: "row" }, h("span", { class: "spinner" }), "Looking for cameras attached to this computer…"));
  (async () => {
    try {
      const r = await api("/api/camera-scan?max_index=6", { timeout: 90000 });
      clear(out).append(r.items.length ? h("div", { class: "stack" }, h("p", {}, `Found ${r.count} camera(s). Pick one to configure it.`),
        ...r.items.map((c) => h("div", { class: "row" }, h("strong", {}, `Camera ${c.index}`), h("span", { class: "muted" }, c.in_use ? "in use by a running class" : `${c.width}×${c.height}`), h("div", { class: "grow" }),
          h("button", { class: "btn btn-sm btn-primary", type: "button", on: { click: () => { dlg.close(); onUse(String(c.index)); } } }, "Use this camera"))))
        : alertBox("warn", "No camera found", "Make sure the camera is plugged in, not used by another app (Zoom, Teams, browser) and that this app has camera permission in the operating-system privacy settings. Network cameras cannot be scanned: add them manually."),
      h("p", { class: "pill-note" }, r.note));
    } catch (e) { clear(out).append(alertBox("err", "Scan failed", humanMessage(e))); }
  })();
}

export function mount(host) {
  const area = h("div", {});
  let m, types = {}, rooms = [];
  const reload = () => m.reload();
  const add = h("button", { class: "btn btn-primary", type: "button", on: { click: () => cameraForm({ rooms, types, onSaved: reload }) } }, icon("plus"), "Add camera");
  const scan = h("button", { class: "btn", type: "button", on: { click: () => scanDialog((idx) => cameraForm({ rooms, types, presetSource: idx, onSaved: reload })) } }, icon("search"), "Scan available cameras");
  host.append(h("div", { class: "toolbar" }, h("p", { class: "muted grow" }, "Cameras are opened only while a class is running. Testing or previewing a camera never records attendance."), scan, add), area);

  m = mountAsync(area, async () => ({ c: await api("/api/cameras"), r: await api("/api/rooms") }), ({ c, r }) => {
    types = c.types; rooms = r.items;
    if (!c.items.length) return emptyState("No cameras configured", "Add a camera, assign it to a room, then press Test to confirm it works.", h("button", { class: "btn btn-primary", type: "button", on: { click: () => cameraForm({ rooms, types, onSaved: reload }) } }, icon("plus"), "Add camera"));
    const full = async (cam) => api("/api/cameras/" + cam.camera_id);
    const act = (x) => { const b = (txt, ic, fn, cls = "") => { const el = h("button", { class: "btn btn-sm " + cls, type: "button" }, icon(ic), txt); el.addEventListener("click", busy(el, fn)); return el; };
      return h("div", { class: "btn-group" },
        b("Test", "play", async () => { const r2 = await api("/api/cameras/test", { method: "POST", json: { camera_id: x.camera_id }, timeout: 40000 }); toast(r2.ok ? `Camera works (${r2.width}×${r2.height}).` : r2.error, r2.ok ? "ok" : "err", 7000); reload(); }),
        b("Preview", "eye", async () => preview(x)),
        b("Edit", "edit", async () => cameraForm({ cam: await full(x), rooms, types, onSaved: reload })),
        b("Reconnect", "refresh", async () => { const r2 = await api("/api/cameras/" + x.camera_id + "/reconnect", { method: "POST", timeout: 40000 }); toast(r2.message, "info"); reload(); }),
        b("Delete", "x", async () => { if (await confirmDialog({ title: "Delete camera?", message: `Delete ${x.name}? Classes in its room will not start until another camera is enabled.`, confirmLabel: "Delete", danger: true })) { await api("/api/cameras/" + x.camera_id, { method: "DELETE" }); toast("Camera deleted.", "ok"); reload(); } })); };
    return card(null, table([
      { label: "Camera", render: (x) => h("div", {}, h("strong", {}, x.name), h("div", { class: "muted ellipsis", title: x.source }, (types[x.camera_type] || x.camera_type) + (x.source ? " · " + x.source : ""))) },
      { label: "Room", render: (x) => x.room_id || h("span", { class: "muted" }, "unassigned") },
      { label: "Status", render: (x) => h("div", {}, badge(x.live || x.status), h("div", { class: "muted small" }, x.last_ok_at ? "last seen " + relTime(x.last_ok_at) : "never connected")) },
      { label: "Health", render: (x) => h("span", { class: x.last_error ? "" : "muted" }, x.last_error ? x.last_error.slice(0, 80) : "no problems recorded", x.reconnect_count ? ` · ${x.reconnect_count} reconnect(s)` : "") },
      { label: "Enabled", render: (x) => {
        const sw = h("input", { type: "checkbox", checked: !!x.enabled, "aria-label": `${x.name} enabled`, disabled: x.camera_type === "none" });
        sw.addEventListener("change", async () => { try { await api("/api/cameras/" + x.camera_id, { method: "PUT", json: { enabled: sw.checked } }); toast(sw.checked ? "Camera enabled." : "Camera disabled.", "ok", 2200); reload(); } catch (e) { sw.checked = !sw.checked; toast(humanMessage(e), "err"); } });
        return h("label", { class: "switch" }, sw); } },
      { label: "Actions", render: act },
    ], c.items, { caption: "Cameras" }), { flush: true });
  }, { poll: 10000 });
  return () => m.stop();
}
