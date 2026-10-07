import { api, humanMessage } from "../api.js";
import { registerCard } from "./register-face.js";
import { h, icon, badge, card, table, mountAsync, emptyState, modal, busy, toast, field, confirmDialog, alertBox, fmtDateTime, clear } from "../ui.js";

const ROLE_NAMES = { admin: "Admin", hod: "HOD / Management", teacher: "Teacher", student: "Student" };

function showCredentials(rows, title = "Share these logins") {
  const csv = "ID,Name,Role,Password\n" + rows.map((r) => [r.id, r.name || "", r.role || "", r.password].map((v) => `"${String(v).replaceAll('"', '""')}"`).join(",")).join("\n");
  const save = h("button", { class: "btn", type: "button" }, "Download CSV");
  save.addEventListener("click", () => { const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); const a = h("a", { href: url, download: "new-logins.csv" }); document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1500); });
  modal({ title, wide: rows.length > 3, body: h("div", { class: "stack" }, alertBox("warn", "Shown only once", "Passwords are stored hashed and cannot be shown again. Hand them out now (people can ask an admin to reset them)."),
    table([{ label: "ID", key: "id", cls: "mono" }, { label: "Name", key: "name" }, { label: "Password", render: (r) => h("span", { class: "mono" }, r.password) }], rows)), actions: [save] });
}

export function mount(host, ctx) {
  const area = h("div", {});
  let m, regInfo = null;
  const creatable = ctx.role === "superadmin" ? ["admin", "hod", "teacher", "student"] : ["hod", "teacher", "student"];
  api("/api/registration-info").then((r) => { regInfo = r; }).catch(() => {});

  function newAccount(prefill = {}) {
    const id = h("input", { type: "text", id: "a-id", maxlength: "100", value: prefill.id || "", required: true, placeholder: "e.g. S101 or T-RAO" });
    const name = h("input", { type: "text", id: "a-name", maxlength: "255", value: prefill.name || "", required: true });
    const role = h("select", { id: "a-role" }, ...creatable.map((r) => h("option", { value: r, selected: r === (prefill.role || "student") }, ROLE_NAMES[r])));
    const pw = h("input", { type: "text", id: "a-pw", autocomplete: "off", placeholder: "leave empty to generate one" });
    const out = h("div", {});
    const save = h("button", { class: "btn btn-primary", type: "button" }, "Create login");
    const dlg = modal({ title: "New login", body: h("div", {}, field("ID", id, "This is what the person types to sign in, and what they use in the face-registration portal."), field("Full name", name), field("Role", role), field("Password", pw, "Students/teachers: 6+ characters. Admins/HODs: 10+."), out), actions: [save] });
    save.addEventListener("click", busy(save, async () => {
      clear(out);
      if (!id.value.trim() || !name.value.trim()) { out.append(alertBox("err", null, "Enter an ID and a name.")); return; }
      let r;
      try { r = await api("/api/accounts", { method: "POST", json: { id: id.value.trim(), name: name.value.trim(), role: role.value, password: pw.value || null } }); }
      catch (e) { out.append(alertBox("err", "Could not create", humanMessage(e))); throw e; }
      dlg.close(); showCredentials([{ id: r.id, name: r.name, role: r.role, password: r.generated_password || pw.value }], "Login created"); m.reload();
    }));
  }

  function bulk() {
    const ta = h("textarea", { id: "b-text", rows: "10", placeholder: "S101, Aarav Mehta\nS102, Diya Nair\n..." });
    const role = h("select", { id: "b-role" }, ...creatable.map((r) => h("option", { value: r, selected: r === "student" }, ROLE_NAMES[r])));
    const out = h("div", {});
    const go = h("button", { class: "btn btn-primary", type: "button" }, "Create logins");
    const dlg = modal({ title: "Add many logins", wide: true, body: h("div", {}, h("p", { class: "muted" }, "One person per line: ID, name. Passwords are generated for everyone."), field("Role for all", role), field("List", ta), out), actions: [go] });
    go.addEventListener("click", busy(go, async () => {
      clear(out);
      const accounts = ta.value.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => { const i = l.search(/[,;\t]/); return i < 0 ? { id: l, name: l } : { id: l.slice(0, i).trim(), name: l.slice(i + 1).trim() }; }).map((a) => ({ ...a, role: role.value }));
      if (!accounts.length) { out.append(alertBox("err", null, "Paste at least one line.")); return; }
      let r;
      try { r = await api("/api/accounts/bulk", { method: "POST", json: { accounts }, timeout: 120000 }); } catch (e) { out.append(alertBox("err", "Failed", humanMessage(e))); throw e; }
      dlg.close(); m.reload();
      if (r.created.length) showCredentials(r.created.map((c) => ({ id: c.id, name: c.name, role: c.role, password: c.generated_password })), `${r.created.length} login(s) created`);
      if (r.failed.length) toast(`${r.failed.length} skipped: ` + r.failed.slice(0, 3).map((f) => `${f.id} (${f.error})`).join("; "), "warn", 12000);
    }));
  }

  function editName(a) {
    const name = h("input", { type: "text", id: "e-name", value: a.name || "", maxlength: "255", required: true });
    const save = h("button", { class: "btn btn-primary", type: "button" }, "Save");
    const dlg = modal({ title: `Edit login ${a.id_number}`, body: h("div", {}, field("Full name", name, "To change the ID, edit the person on the Students/Teachers page (after they register a face), or delete this login and create a new one.")), actions: [save] });
    save.addEventListener("click", busy(save, async () => {
      if (!name.value.trim()) { toast("Enter a name.", "warn"); return; }
      await api("/api/accounts/" + encodeURIComponent(a.id_number), { method: "PUT", json: { name: name.value.trim() } });
      dlg.close(); toast("Saved.", "ok"); m.reload();
    }));
  }

  async function reset(a) {
    const r = await api(`/api/accounts/${encodeURIComponent(a.id_number)}/password`, { method: "PUT", json: {} });
    showCredentials([{ id: a.id_number, name: a.name, role: a.role, password: r.generated_password }], "Password reset");
  }

  host.append(h("div", { class: "toolbar" }, h("p", { class: "muted grow" }, "Create the ID and password each person uses to sign in, and to register their face. Passwords are stored hashed."),
    h("button", { class: "btn", type: "button", on: { click: bulk } }, icon("upload"), "Add many"), h("button", { class: "btn btn-primary", type: "button", on: { click: () => newAccount() } }, icon("plus"), "New login")), area);

  m = mountAsync(area, async () => ({ a: await api("/api/accounts"), u: await api("/api/users") }), ({ a, u }) => {
    const ids = new Set(a.items.map((x) => x.id_number));
    const noFace = a.items.filter((x) => (x.role === "student" || x.role === "teacher") && !x.face_registered);
    const noLogin = u.items.filter((x) => (x.role === "student" || x.role === "teacher") && !ids.has(x.roll_no));
    const portal = regInfo?.enabled ? h("span", {}, "Registration portal: ", h("a", { href: regInfo.student_url, target: "_blank", rel: "noopener" }, "students"), " · ", h("a", { href: regInfo.teacher_url, target: "_blank", rel: "noopener" }, "teachers")) : h("span", { class: "muted" }, "Registration portal is disabled.");
    return h("div", { class: "stack" },
      h("div", { class: "grid kpis" },
        h("div", { class: "card kpi" }, h("div", { class: "label" }, icon("key"), "Logins"), h("div", { class: "value" }, String(a.count))),
        h("div", { class: "card kpi" }, h("div", { class: "label" }, icon("student"), "Faces registered"), h("div", { class: "value" }, String(a.items.filter((x) => x.face_registered).length)), h("div", { class: "sub" }, `${noFace.length} still to register`)),
        h("div", { class: "card kpi" }, h("div", { class: "label" }, icon("camera"), "Face registration"), h("div", { class: "sub" }, portal), h("div", { class: "sub" }, "People open it on their phone or laptop, sign in with their ID and password, and follow the on-screen head-movement steps."))),
      regInfo?.enabled ? card("Face registration QR codes", h("div", { class: "card-body grid two" }, registerCard("student", regInfo, { compact: true }), registerCard("teacher", regInfo, { compact: true })), { flush: false }) : null,
      noFace.length ? alertBox("info", `${noFace.length} login(s) have not registered a face yet`, noFace.slice(0, 12).map((x) => x.id_number).join(", ") + (noFace.length > 12 ? " …" : "") + ". They cannot be recognised by the cameras until they do.") : null,
      noLogin.length ? alertBox("warn", `${noLogin.length} registered person(s) have no login`, noLogin.slice(0, 12).map((x) => x.roll_no).join(", ") + ". Create a login so they can see their attendance.") : null,
      card("Logins", a.items.length ? table([
        { label: "ID", key: "id_number", cls: "mono" }, { label: "Name", key: "name" }, { label: "Role", render: (x) => h("span", { class: "chip" }, ROLE_NAMES[x.role] || x.role) },
        { label: "Face", render: (x) => (x.role === "student" || x.role === "teacher") ? (x.face_registered ? badge("present", "Registered") : badge("warning", "Not yet")) : h("span", { class: "muted" }, "n/a") },
        { label: "Created", render: (x) => fmtDateTime(x.created_at) },
        { label: "", cls: "actions", render: (x) => h("span", { class: "btn-group" },
          h("button", { class: "btn btn-sm", type: "button", "aria-label": `Edit ${x.id_number}`, on: { click: () => editName(x) } }, icon("edit"), "Edit"),
          h("button", { class: "btn btn-sm", type: "button", on: { click: (e) => busy(e.currentTarget, () => reset(x))() } }, "Reset password"),
          h("button", { class: "btn btn-sm", type: "button", "aria-label": `Delete login ${x.id_number}`, on: { click: async () => {
            if (!(await confirmDialog({ title: "Delete this login?", message: `${x.name || x.id_number} will no longer be able to sign in. Their face registration and attendance history are kept.`, confirmLabel: "Delete login", danger: true }))) return;
            try { await api("/api/accounts/" + encodeURIComponent(x.id_number), { method: "DELETE" }); toast("Login deleted.", "ok"); m.reload(); } catch (e) { toast(humanMessage(e), "err"); } } } }, "Delete")) },
      ], a.items, { caption: "Logins" }) : emptyState("No logins yet", "Create logins for your teachers and students, then have them register their face.", h("button", { class: "btn btn-primary", type: "button", on: { click: () => newAccount() } }, icon("plus"), "New login")), { flush: true }));
  });
  return () => m.stop();
}
