import { api, ApiError, humanMessage } from "../api.js";
import { h, icon, badge, card, table, mountAsync, emptyState, modal, busy, toast, alertBox, confirmDialog, cap, clear } from "../ui.js";

const DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];

function uploadWizard(onPublished) {
  const body = h("div", { class: "stack" });
  const file = h("input", { type: "file", id: "tt-file", accept: ".pdf,.xlsx,.xls,.csv,.docx,.png,.jpg,.jpeg", class: "sr-only" });
  const drop = h("label", { class: "dropzone", for: "tt-file" }, icon("upload"), h("div", {}, h("strong", {}, "Choose a timetable file"), " or drop it here"), h("div", { class: "muted" }, "PDF, Excel, CSV, Word or an image · up to 15 MB. Nothing changes until you review and publish."));
  const status = h("div", {});
  body.append(drop, file, status);
  let m;

  async function handle(f) {
    if (!f) return;
    clear(status).append(h("div", { class: "row" }, h("span", { class: "spinner" }), `Reading ${f.name}… scanned PDFs can take a minute.`));
    const form = new FormData(); form.append("file", f);
    try {
      const d = await api("/api/timetable/preview", { method: "POST", form, timeout: 300000 });
      showPreview(d);
    } catch (err) { clear(status).append(alertBox("err", "Could not read the file", humanMessage(err))); }
  }
  file.addEventListener("change", () => handle(file.files[0]));
  for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); });
  for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); });
  drop.addEventListener("drop", (e) => handle(e.dataTransfer.files[0]));

  function showPreview(d) {
    clear(body);
    const byRow = {};
    for (const i of d.issues) (byRow[i.row] ||= []).push(i);
    body.append(
      h("div", { class: "row" }, badge(d.errors ? "ERROR" : "READY", d.errors ? `${d.errors} error(s)` : "No errors"), d.warnings ? badge("WARNING", `${d.warnings} warning(s)`) : null, h("span", { class: "muted" }, `${d.rows.length} valid row(s) found. This is a preview — the live timetable is unchanged.`)),
      d.issues.length ? h("ul", { class: "timeline" }, ...d.issues.slice(0, 40).map((i) => h("li", {}, badge(i.severity === "error" ? "ERROR" : "WARNING"), h("div", { class: "what" }, `Row ${i.row}: ${i.message}`)))) : null,
      d.rows.length ? table([{ label: "Day", render: (r) => cap(r.day) }, { label: "Time", render: (r) => `${r.start}–${r.end}` }, { label: "Subject", key: "subject" }, { label: "Teacher", key: "teacher" }, { label: "Room", key: "room" }], d.rows) : emptyState("Nothing usable", "No row passed validation."),
    );
    const publish = h("button", { class: "btn btn-primary", type: "button", disabled: d.errors > 0 || !d.rows.length }, "Review and publish…");
    publish.addEventListener("click", busy(publish, async () => {
      const current = await api("/api/timetable");
      const ok = await confirmDialog({ title: "Replace the timetable?", confirmLabel: "Publish timetable", danger: true,
        message: `This replaces the ${current.count} current slot(s) with ${d.rows.length} new slot(s). The previous timetable is archived in the database and existing section assignments are kept for unchanged slots.` });
      if (!ok) return;
      await api("/api/timetable/publish", { method: "POST", json: { draft_id: d.draft_id, confirm: true } });
      toast("Timetable published.", "ok"); m.close(); onPublished();
    }));
    const discard = h("button", { class: "btn", type: "button" }, "Discard draft");
    discard.addEventListener("click", busy(discard, async () => { await api("/api/timetable/drafts/" + d.draft_id, { method: "DELETE" }); m.close(); }));
    body.append(h("div", { class: "row" }, publish, discard));
  }
  m = modal({ title: "Upload timetable", body, wide: true });
  return m;
}

export function mount(host, ctx) {
  const ro = !ctx.canEdit;
  const area = h("div", {});
  let mm;
  const uploadBtn = () => h("button", { class: "btn btn-primary", type: "button", on: { click: () => uploadWizard(() => mm.reload()) } }, icon("upload"), "Upload timetable");
  const up = uploadBtn();
  host.append(h("div", { class: "toolbar" }, h("p", { class: "muted grow" }, "Classes start automatically from this timetable. Each slot needs a room with a camera, a registered teacher and a section of students."), ro ? null : up), area);

  mm = mountAsync(area, async () => ({ tt: await api("/api/timetable"), sec: await api("/api/sections") }), ({ tt, sec }) => {
    if (!tt.items.length) return emptyState("No timetable yet", ro ? "An administrator has not published a timetable yet." : "Upload a timetable file to preview, validate and publish it.", ro ? null : uploadBtn());
    const blocks = DAYS.map((day) => {
      const rows = tt.items.filter((r) => r.day_of_week === day);
      if (!rows.length) return null;
      return card(cap(day), table([
        { label: "Time", render: (r) => h("span", { class: "nowrap mono" }, `${r.start_time}–${r.end_time}`) }, { label: "Subject", key: "subject" },
        { label: "Teacher", render: (r) => r.teacher_name || r.teacher_id }, { label: "Room", key: "room_id" },
        { label: "Section", render: (r) => {
          const sel = h("select", { "aria-label": `Section for ${r.subject} ${r.start_time}`, disabled: ro }, h("option", { value: "" }, "— none (no student attendance) —"), ...sec.items.map((s) => h("option", { value: s.section_id, selected: s.section_id === r.section_id }, s.section_name)));
          sel.addEventListener("change", async () => { try { await api(`/api/timetable/${r.id}/section`, { method: "PUT", json: { section_id: sel.value || null } }); toast("Section saved.", "ok", 2200); } catch (e) { toast(humanMessage(e), "err"); mm.reload(); } });
          return sel; } },
      ], rows, { caption: cap(day) }), { flush: true });
    }).filter(Boolean);
    return h("div", { class: "stack" }, sec.items.length ? null : alertBox("warn", "No sections exist yet", "Create sections and enrol students on the Students page, then assign them to slots here."), ...blocks);
  });
  return () => mm.stop();
}
