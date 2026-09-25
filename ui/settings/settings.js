const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const call = (m, a = {}) => fetch(`/api/${m}`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(a)}).then((r) => r.json());
const fmtDay = (iso) => iso ? new Date(iso + "T12:00").toLocaleDateString([], {weekday: "short", month: "short", day: "numeric"}) : "";
let d = null;

async function load() {
  d = await call("overview");
  render();
  renderUpdates(d.updates);
  const [rm, pr] = await Promise.all([call("check_rm"), call("printers")]);
  renderChecks(rm);
  renderParks(rm);
  if (pr.ok) $("printer").innerHTML = `<option value="">Don't print — just save the PDF</option>` +
    pr.printers.filter((p) => !/fax|onenote|xps/i.test(p)).map((p) => `<option ${p === d.printer ? "selected" : ""}>${esc(p)}</option>`).join("");
}

function renderChecks(rm) {
  const t = d.tailscale, items = [
    [d.rm_set_up && rm.ok, rm.ok ? "Connected to Rent Manager" : `Rent Manager: ${rm.error || "not set up — run install.ps1"}`],
    [d.server_up, d.server_up ? "Phone server is running" : "Phone server isn't running — restart the computer, or run install.ps1 again"],
    [t.installed, t.installed ? "Tailscale is installed" : "Tailscale isn't installed — see SETUP.md step 3"],
    [t.signed_in, t.signed_in ? "Tailscale is signed in" : "Tailscale isn't signed in — open Tailscale from the Start menu and sign in"],
    [!!t.url, t.url ? "The phone can reach this computer from anywhere" : "Funnel isn't on — see SETUP.md step 4"],
    [d.user.has_password, d.user.has_password ? "A phone password is set" : "No phone password yet — set one below"],
    [d.parks.length > 0, d.parks.length ? `Parks: ${d.parks.join(", ")}` : "No park picked yet — tick one below"],
  ];
  $("checks").innerHTML = items.map(([ok, text]) => `<li class="${ok ? "ok" : "bad"}">${esc(text)}</li>`).join("");
  $("phone").innerHTML = t.url ? `<div class="phonebox">${t.qr ? `<img src="${t.qr}" alt="QR code for the phone">` : ""}
    <div>On the phone, scan this with the camera or type<br><code>${esc(t.url)}</code><br>then sign in as <b>${esc(d.user.username)}</b>
    and add it to the home screen (iPhone: Share → Add to Home Screen; Android: ⋮ → Add to Home screen).</div></div>` : "";
}

function renderParks(rm) {
  if (!rm.ok) { $("parks").innerHTML = `<span class="bad">Can't list parks until Rent Manager is connected.</span>`; return; }
  $("parks").innerHTML = rm.parks.map((p) => `<label class="check"><input type="checkbox" value="${esc(p)}" ${d.parks.includes(p) ? "checked" : ""}> ${esc(p)}</label>`).join("");
  for (const b of $("parks").querySelectorAll("input")) b.onchange = async () => {
    d = await call("save", {parks: [...$("parks").querySelectorAll("input:checked")].map((x) => x.value)});
    renderChecks(rm);
  };
}

function render() {
  $("name").value = d.user.name; $("username").value = d.user.username;
  $("printPhotos").checked = !!d.print_photos;
  const live = d.mode === "live";
  $("mode").innerHTML = `<span class="pill ${live ? "live" : "test"}">${live ? "LIVE — real residents, real printing" : "TEST — notices go on the test record, nothing prints"}</span>
    <button class="btn ${live ? "ghost" : "live"}" id="modeBtn">${live ? "Switch back to test" : "Go live"}</button>`;
  $("modeBtn").onclick = async (e) => {
    if (!live && !e.target.classList.contains("armed")) { e.target.classList.add("armed"); e.target.textContent = "Click again — real residents, real printing"; return; }
    d = await call("set_mode", {mode: live ? "test" : "live"}); render();
  };
  const rows = d.items.concat([{id: "__other", label: "Something else (typed in)", ...d.other}]);
  $("items").innerHTML = `<tr><th>Violation</th><th>Days</th><th>Then</th><th>Written today → due</th></tr>` + rows.map((it) =>
    `<tr data-id="${it.id}"><td>${esc(it.label)}</td><td><input type="number" min="0" max="90" value="${it.days}" class="days"></td>
     <td><select class="rule"><option value="exact" ${it.rule === "exact" ? "selected" : ""}>that day</option>
       <option value="next_monday" ${it.rule === "next_monday" ? "selected" : ""}>then next Monday</option></select></td>
     <td class="ex">${fmtDay(it.example)}</td></tr>`).join("");
  $("recent").innerHTML = d.recent.length ? d.recent.map((r) => `<div>${esc(r.issued)} · ${esc(r.park)} lot ${esc(r.lot)} · ${esc(r.tenant_name)} ·
    ${esc(r.warning)} · by ${esc(fmtDay(r.correct_by))}${r.test ? " · test" : ""}</div>`).join("") : "None yet.";
}

const fmtWhen = (iso) => iso ? new Date(iso).toLocaleString([], {weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}) : "";
let updTimer = null;

function renderUpdates(u) {
  if (u.dev_copy) {
    $("upd").innerHTML = `<p class="hint">This is Codi's development copy — it's where new versions come from, so it doesn't update itself.</p>`;
    $("updNow").hidden = true; return;
  }
  const ver = u.version ? `Version <b>${esc(u.version)}</b>, put in ${esc(fmtWhen(u.installed_at))}.` : "Version: as installed.";
  const last = u.last_check ? ` Last checked ${esc(fmtWhen(u.last_check))}:` : "";
  $("upd").innerHTML = `<p>${ver}${last} <span class="${u.problem ? "bad" : ""}">${esc(u.checking ? "checking now…" : (u.result || "not checked yet."))}</span></p>`;
  clearTimeout(updTimer);
  if (u.checking) updTimer = setTimeout(async () => renderUpdates(await call("update_status")), 3000);
}

$("updNow").onclick = async () => {
  await call("check_updates");
  renderUpdates({...(await call("update_status")), checking: true});
};
$("saveYou").onclick = async () => { d = await call("save", {name: $("name").value, username: $("username").value}); $("youMsg").innerHTML = `<span class="ok">Saved.</span>`; };
$("savePw").onclick = async () => {
  const pw = $("pw").value;
  if (pw.length < 12) { $("pwMsg").innerHTML = `<span class="bad">Not saved — that's ${pw.length} characters; it needs at least 12.</span>`; return; }
  const r = await call("set_password", {password: pw});
  $("pw").value = "";
  $("pwMsg").innerHTML = r.ok ? `<span class="ok">✓ Saved at ${new Date().toLocaleTimeString([], {hour: "numeric", minute: "2-digit"})}. Any phone signed in before has to sign in again.</span>`
    : `<span class="bad">Not saved — ${esc(r.error)}</span>`;
  d = await call("overview"); load();
};
$("printer").onchange = async () => { d = await call("save", {printer: $("printer").value}); $("printMsg").textContent = "Printer saved."; };
$("printPhotos").onchange = async () => { d = await call("save", {print_photos: $("printPhotos").checked}); };
$("testPrint").onclick = async () => {
  $("printMsg").textContent = "Printing…";
  const r = await call("test_print", {printer: $("printer").value});
  $("printMsg").innerHTML = r.ok ? `<span class="ok">Sent to ${esc(r.printer)}.</span>` : `<span class="bad">${esc(r.error || "Didn't print.")}</span>`;
};
$("saveItems").onclick = async () => {
  const items = [], other = {};
  for (const tr of $("items").querySelectorAll("tr[data-id]")) {
    const row = {id: tr.dataset.id, days: tr.querySelector(".days").value, rule: tr.querySelector(".rule").value};
    if (row.id === "__other") Object.assign(other, row); else items.push(row);
  }
  d = await call("save", {items, other}); render();
};

load();
