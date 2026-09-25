// Violations phone app — one step at a time, one screen per step, no scrolling.
// Every step is saved on the manager computer as soon as it's done
// (drafts.py), so a reload never loses work. No AI anywhere (Codi's rule).

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const STEPS = {"s-photo": 1, "s-circle": 2, "s-lot": 3, "s-items": 4, "s-other": 4, "s-send": 5};
const TITLES = {"s-photo": "Photo", "s-circle": "Circle it", "s-lot": "Which lot?", "s-items": "What's wrong?",
                "s-other": "Something else", "s-send": "Warning & send", "s-fix": "Photo of the fix",
                "s-fixok": "It's fixed"};
const lotName = (l) => (l || "").replace(/^0+(?=\d)/, "");

let me = null, draft = null, park = null, parks = [], level = null;
let autoResume = true;            // only on first load, not after "Throw it away"
let current = null;

function show(id) {
  if (id !== "s-photo" && id !== "s-fix") stopCamera();     // never leave the camera on
  current = id;
  for (const s of document.querySelectorAll(".screen")) s.hidden = s.id !== id;
  const step = STEPS[id], fixing = id === "s-fix" || id === "s-fixok";
  $("stepBar").hidden = !step;
  if (step) {
    $("stepText").textContent = `Step ${step} of 5`;
    $("dots").innerHTML = [1, 2, 3, 4, 5].map((n) => `<i class="${n <= step ? "on" : ""}"></i>`).join("");
  }
  $("backBtn").hidden = !fixing && (!step || step === 1);
  $("logo").hidden = !!step || fixing;
  $("title").textContent = TITLES[id] || "Violations";
  $("cancelBtn").hidden = !step;
}

async function api(path, opts = {}) {
  if (opts.json !== undefined) {
    opts = {...opts, method: opts.method || "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(opts.json)};
    delete opts.json;
  }
  const r = await fetch(path, {credentials: "same-origin", ...opts});
  let data = {};
  try { data = await r.json(); } catch { data = {ok: false, error: `The manager computer answered ${r.status}.`}; }
  if (r.status === 401 && data.error === "signin") { toSignin(); throw new Error("signin"); }
  return data;
}

// ---- sign in -----------------------------------------------------------------
function toSignin() {
  show("signin"); $("foot").hidden = true; $("who").textContent = "";
  fetch("/api/version").then((r) => r.json()).then((v) => { $("signinVer").textContent = verText(v); }).catch(() => {});
}
// Which version this park's computer runs — so Codi can see at a glance which parks are up to date.
function verText(v) {
  if (!v || !v.version) return v && v.dev ? "Development copy" : "Version: as installed (updates within the hour)";
  const when = v.installed_at ? " · updated " + new Date(v.installed_at).toLocaleDateString([], {month: "short", day: "numeric"}) : "";
  return `${v.dev ? "Development copy" : "Version"} ${v.version}${when}`;
}
$("signinForm").onsubmit = async (e) => {
  e.preventDefault();
  $("signinErr").textContent = "";
  const r = await fetch("/api/login", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({username: $("user").value, password: $("pass").value})}).then((r) => r.json());
  if (!r.ok) { $("signinErr").textContent = r.error; return; }
  $("pass").value = "";
  start();
};
$("signout").onclick = async () => { await fetch("/api/logout", {method: "POST"}); toSignin(); };

async function start() {
  try { me = await api("/api/me"); } catch { return; }
  if (!me.ok) { toSignin(); return; }
  $("who").textContent = me.name;
  $("testBanner").hidden = me.mode === "live";
  $("foot").hidden = false;
  $("footVer").textContent = verText(me.version);
  home();
}

// ---- home: active violations, paged to fit the screen -------------------------
let active = [], page = 0;
const dueText = (d) => d > 1 ? `due in ${d} days` : d === 1 ? "due tomorrow" : d === 0 ? "due today" : `${-d} day${d === -1 ? "" : "s"} overdue`;
const dueClass = (d) => d < 0 ? "late" : d <= 1 ? "soon" : "";
const fmtDay = (iso) => new Date(iso + "T12:00").toLocaleDateString([], {weekday: "short", month: "short", day: "numeric"});

async function home() {
  show("home");
  draft = null;
  const r = await api("/api/home");
  if (!r.ok) { $("active").innerHTML = `<p class="err">${esc(r.error)}</p>`; return; }
  $("parkName").textContent = `· ${r.parks.join(" & ")}`;
  // Back after a reload mid-violation? Go straight into it instead of asking.
  if (r.draft && autoResume && Date.now() - new Date(r.draft.updated).getTime() < 3600e3) {
    autoResume = false;
    return resume(r.draft);
  }
  autoResume = false;
  $("resume").innerHTML = r.draft ? `<div class="card resume"><b>You were in the middle of one.</b>
      <div class="row"><button class="btn primary grow" id="resumeBtn">Carry on</button><button class="btn grow" id="discardBtn">Throw it away</button></div></div>` : "";
  if (r.draft) {
    $("resumeBtn").onclick = () => resume(r.draft);
    $("discardBtn").onclick = async () => { await api(`/api/drafts/${r.draft.id}/cancel`, {json: {}}); home(); };
  }
  active = r.active; page = 0;
  renderActive();
}

function perPage() {
  const h = $("active").clientHeight;
  return Math.max(1, Math.floor((h + 8) / (128 + 8)));
}

function renderActive() {
  if (!active.length) {
    $("pager").hidden = true;
    $("active").innerHTML = `<p class="muted center">No active violations.</p>`;
    return;
  }
  const n = perPage(), pages = Math.ceil(active.length / n);
  page = Math.min(page, pages - 1);
  $("pager").hidden = pages < 2;
  $("pageText").textContent = `${page * n + 1}–${Math.min(active.length, (page + 1) * n)} of ${active.length}`;
  $("prevPage").disabled = page === 0; $("nextPage").disabled = page >= pages - 1;
  $("active").innerHTML = active.slice(page * n, (page + 1) * n).map((v) => `
    <div class="card vio">
      <div class="vhead"><b>Lot ${esc(v.lot)}</b> <span class="muted name">${esc(v.tenant_name)}</span>
        <span class="due-chip ${dueClass(v.days_left)}">${dueText(v.days_left)}</span></div>
      <div class="vwhat">${esc(v.what)}</div>
      <div class="vmeta muted">${esc(["Reminder", "Final"].includes(v.warning) ? v.warning : "Warning " + v.warning)} · by ${esc(fmtDay(v.correct_by))}${v.test ? " · test" : ""}</div>
      <div class="row">
        ${v.has_pdf ? `<a class="btn grow" href="/api/letters/${esc(v.ref)}.pdf" target="_blank" rel="noopener">Notice</a>` : ""}
        <button class="btn grow" data-fixed="${v.history_id}">It's fixed ✓</button>
      </div>
    </div>`).join("");
  for (const b of document.querySelectorAll("[data-fixed]")) b.onclick = () =>
    startFix(active.find((v) => String(v.history_id) === b.dataset.fixed));
}
$("prevPage").onclick = () => { page--; renderActive(); };
$("nextPage").onclick = () => { page++; renderActive(); };

$("addBtn").onclick = async () => {
  const r = await api("/api/drafts", {json: {}});
  if (!r.ok) { alert(r.error); return; }
  draft = r.draft;
  toStep();
};
$("homeBtn").onclick = home;
$("cancelBtn").onclick = async () => {
  if (!confirm("Throw away this violation? Nothing has been saved to Rent Manager yet.")) return;
  if (draft) await api(`/api/drafts/${draft.id}/cancel`, {json: {}});
  home();
};
$("backBtn").onclick = async () => {
  if (current === "s-fix") return home();
  if (current === "s-fixok") return startFix(fixing);
  if (current === "s-send" || current === "s-other") return stepItems(false);
  const r = await api(`/api/drafts/${draft.id}/back`, {json: {}});
  if (r.ok) { draft = r.draft; toStep(); }
};

function resume(d) { draft = d; toStep(); }
function toStep() {
  const s = draft.stage;
  if (s === "photo" || !draft.photo) return stepPhoto();
  if (s === "circle") return stepCircle();
  if (s === "lot") return stepLot();
  return stepItems(true);
}

// ---- step 1: photo, with the camera inside the page -----------------------------
// Handing off to the phone's camera app made the browser reload this page on
// the way back, losing the photo before it uploaded.
let stream = null;

function stepPhoto() { $("photoMsg").textContent = ""; show("s-photo"); startCamera(); }

// One camera, used by two screens: the violation photo and the "fixed" photo.
const CAMS = {violation: {video: "viewfinder", msg: "vfMsg", shutter: "shutter", alt: "“Phone's camera app”"},
              fix: {video: "fixVideo", msg: "fixMsg", shutter: "fixShutter", alt: "“Photo already taken”"}};

async function startCamera(which = "violation") {
  const k = CAMS[which];
  stopCamera();
  $(k.shutter).disabled = true;
  $(k.msg).hidden = false; $(k.msg).textContent = "Starting the camera…";
  if (!navigator.mediaDevices?.getUserMedia) {
    $(k.msg).textContent = `This browser can't show the camera here — use ${k.alt} below.`;
    return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio: false, video: {
      facingMode: {ideal: "environment"}, width: {ideal: 2560}, height: {ideal: 1920}}});
    const v = $(k.video);
    v.srcObject = stream;
    await v.play().catch(() => {});
    $(k.msg).hidden = true;
    $(k.shutter).disabled = false;
  } catch (e) {
    $(k.msg).textContent = e.name === "NotAllowedError"
      ? `The camera is blocked for this page. Allow it in your browser settings, or use ${k.alt} below.`
      : `Couldn't start the camera — use ${k.alt} below.`;
  }
}

function stopCamera() {
  if (stream) { for (const t of stream.getTracks()) t.stop(); stream = null; }
  for (const k of Object.values(CAMS)) { const v = $(k.video); if (v) v.srcObject = null; }
}

async function snap(which) {
  const k = CAMS[which], v = $(k.video);
  if (!v.videoWidth) return null;
  $(k.shutter).disabled = true;
  const c = document.createElement("canvas");
  c.width = v.videoWidth; c.height = v.videoHeight;
  c.getContext("2d").drawImage(v, 0, 0);
  const blob = await new Promise((res) => c.toBlob(res, "image/jpeg", 0.9));
  stopCamera();
  return blob;
}

$("shutter").onclick = async () => {
  const blob = await snap("violation");
  if (!blob) return;
  $("vfMsg").hidden = false; $("vfMsg").textContent = "Saving the photo…";
  await sendPhoto(blob);
  if (current === "s-photo") startCamera();       // upload failed — try again
};

// ---- "It's fixed": photo of the fix, then a note in History & Notes -------------------
let fixing = null, fixBlob = null;

function fixWho(v) {
  return `<div><b>Lot ${esc(v.lot)}</b> · ${esc(v.tenant_name)}<div class="what">${esc(v.what)}</div></div>`;
}

function startFix(v) {
  fixing = v; fixBlob = null;
  $("fixWho").innerHTML = fixWho(v);
  show("s-fix");
  startCamera("fix");
}

$("fixShutter").onclick = async () => {
  const blob = await snap("fix");
  if (blob) reviewFix(await shrink(blob));
};
$("fixLibrary").onchange = async (e) => {
  const f = e.target.files[0]; e.target.value = "";
  if (f) reviewFix(await shrink(f));
};
$("fixNoPhoto").onclick = () => reviewFix(null);

function reviewFix(blob) {
  fixBlob = blob;
  $("fixWho2").innerHTML = fixWho(fixing);
  const img = $("fixPreview");
  if (img.src) URL.revokeObjectURL(img.src);
  img.hidden = !blob; $("fixNoPic").hidden = !!blob;
  if (blob) img.src = URL.createObjectURL(blob); else img.removeAttribute("src");
  $("fixErr").textContent = "";
  $("fixSave").disabled = false; $("fixSave").textContent = blob ? "Save — it's fixed" : "Save without a photo";
  show("s-fixok");
}
$("fixRetake").onclick = () => startFix(fixing);

$("fixSave").onclick = async () => {
  $("fixSave").disabled = true; $("fixSave").textContent = "Saving…";
  let r;
  try {
    if (fixBlob) {
      const fd = new FormData();
      fd.append("photo0", fixBlob, "fixed.jpg");
      r = await api(`/api/violations/${fixing.history_id}/fixed`, {method: "POST", body: fd});
    } else {
      r = await api(`/api/violations/${fixing.history_id}/fixed`, {json: {}});
    }
  } catch { r = {ok: false, error: "Couldn't reach the manager computer — check your signal and try again."}; }
  if (!r.ok) { $("fixErr").textContent = r.error; $("fixSave").disabled = false; $("fixSave").textContent = "Try again"; return; }
  fixing = null; fixBlob = null;
  home();
};

async function shrink(file, edge = 2000) {
  try {
    const bmp = await createImageBitmap(file);
    const r = Math.min(1, edge / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * r); c.height = Math.round(bmp.height * r);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    return await new Promise((res) => c.toBlob(res, "image/jpeg", 0.88));
  } catch { return file; }
}

async function sendPhoto(file) {
  if (!file) return;
  $("photoMsg").textContent = "Saving the photo…";
  const fd = new FormData();
  fd.append("photo0", await shrink(file), "photo.jpg");
  let r;
  try { r = await api(`/api/drafts/${draft.id}/photo`, {method: "POST", body: fd}); }
  catch { $("photoMsg").textContent = "Couldn't reach the manager computer — check your signal and try again."; return; }
  if (!r.ok) { $("photoMsg").textContent = r.error; return; }
  $("photoMsg").textContent = "";
  draft = r.draft;
  stepCircle();
}
$("camera").onchange = (e) => { sendPhoto(e.target.files[0]); e.target.value = ""; };
$("library").onchange = (e) => { sendPhoto(e.target.files[0]); e.target.value = ""; };

// ---- step 2: circle the problem --------------------------------------------------
let strokes = [], img = null, drawing = null;

function stepCircle() {
  show("s-circle");
  strokes = [];
  img = new Image();
  img.onload = () => { sizeCanvas(); redraw(); };
  img.src = `/api/drafts/${draft.id}/photo?t=${Date.now()}`;
}

function sizeCanvas() {
  const c = $("canvas"), wrap = $("canvasWrap");
  const r = Math.min(wrap.clientWidth / img.width, wrap.clientHeight / img.height);
  const dpr = window.devicePixelRatio || 1;
  c.style.width = `${img.width * r}px`; c.style.height = `${img.height * r}px`;
  c.width = Math.round(img.width * r * dpr); c.height = Math.round(img.height * r * dpr);
}

function redraw() {
  const c = $("canvas"), g = c.getContext("2d");
  g.drawImage(img, 0, 0, c.width, c.height);
  g.strokeStyle = "#e61e28"; g.lineWidth = Math.max(4, c.width / 110); g.lineCap = "round"; g.lineJoin = "round";
  for (const s of strokes.concat(drawing ? [drawing] : [])) {
    g.beginPath();
    s.forEach(([x, y], i) => (i ? g.lineTo(x * c.width, y * c.height) : g.moveTo(x * c.width, y * c.height)));
    g.stroke();
  }
  $("circleNext").disabled = !strokes.length;
}

function pt(e) {
  const r = $("canvas").getBoundingClientRect();
  return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
}
$("canvas").addEventListener("pointerdown", (e) => { e.preventDefault(); $("canvas").setPointerCapture(e.pointerId); drawing = [pt(e)]; redraw(); });
$("canvas").addEventListener("pointermove", (e) => { if (!drawing) return; e.preventDefault(); drawing.push(pt(e)); redraw(); });
const endStroke = () => { if (drawing && drawing.length > 2) strokes.push(drawing); drawing = null; redraw(); };
$("canvas").addEventListener("pointerup", endStroke);
$("canvas").addEventListener("pointercancel", endStroke);
$("undoBtn").onclick = () => { strokes.pop(); redraw(); };
$("clearBtn").onclick = () => { strokes = []; redraw(); };
$("retakeBtn").onclick = stepPhoto;

$("circleNext").onclick = async () => {
  $("circleNext").disabled = true; $("circleNext").textContent = "Saving…";
  const r = await api(`/api/drafts/${draft.id}/marks`, {json: {strokes: strokes.map((s) => s.map(([x, y]) => [+x.toFixed(4), +y.toFixed(4)]))}});
  $("circleNext").textContent = "Next";
  if (!r.ok) { alert(r.error); $("circleNext").disabled = false; return; }
  draft = r.draft;
  stepLot();
};

// ---- step 3: lot, typed on a keypad ---------------------------------------------
let digits = "", lotPick = null, tenantPick = null;

async function stepLot() {
  show("s-lot");
  digits = ""; lotPick = null; tenantPick = null;
  if (!parks.length) parks = (await api("/api/parks")).parks;
  if (!park) park = parks[0];
  // Two or more parks on this computer: a row of park buttons above the keypad.
  $("parkPick").hidden = parks.length < 2;
  $("parkPick").innerHTML = parks.map((p, i) =>
    `<button data-i="${i}" aria-pressed="${p === park}">${esc(p.park)}</button>`).join("");
  for (const b of $("parkPick").querySelectorAll("button")) b.onclick = () => {
    park = parks[+b.dataset.i];
    for (const x of $("parkPick").querySelectorAll("button")) x.setAttribute("aria-pressed", String(x === b));
    renderLot();
  };
  renderLot();
}

function renderLot() {
  $("lotDigits").textContent = digits || "–";
  const who = $("lotWho");
  who.className = "lotwho";
  lotPick = digits ? park.lots.find((l) => lotName(l.lot) === digits) : null;
  if (!digits) { who.textContent = "Type the lot number"; tenantPick = null; }
  else if (!lotPick) { who.classList.add("bad"); who.textContent = `No lot ${digits} at ${park.park}`; tenantPick = null; }
  else if (!lotPick.tenants.length) { who.classList.add("bad"); who.textContent = `Lot ${digits} is vacant`; tenantPick = null; }
  else if (lotPick.tenants.length === 1) { who.classList.add("ok"); tenantPick = lotPick.tenants[0]; who.textContent = tenantPick.name; }
  else {
    if (!lotPick.tenants.includes(tenantPick)) tenantPick = lotPick.tenants[0];
    who.innerHTML = `<div class="tenantpick">${lotPick.tenants.map((t, i) =>
      `<button data-i="${i}" aria-pressed="${t === tenantPick}">${esc(t.name)}</button>`).join("")}</div>`;
    for (const b of who.querySelectorAll("button")) b.onclick = () => { tenantPick = lotPick.tenants[+b.dataset.i]; renderLot(); };
  }
  $("lotNext").disabled = !tenantPick;
}

for (const b of $("keypad").querySelectorAll("button")) b.onclick = () => {
  const k = b.dataset.k;
  if (k === "del") digits = digits.slice(0, -1);
  else if (k === "clr") digits = "";
  else if (digits.length < 4) digits = (digits + k).replace(/^0+(?=\d)/, "");
  renderLot();
};

$("lotNext").onclick = async () => {
  $("lotNext").disabled = true;
  const r = await api(`/api/drafts/${draft.id}/lot`, {json: {property_id: park.property_id, unit_id: lotPick.unit_id, tenant_id: tenantPick.id}});
  if (!r.ok) { alert(r.error); renderLot(); return; }
  draft = r.draft;
  stepItems(true);
};

// ---- step 4: what's wrong (tiles) ------------------------------------------------
let picked = new Set(), others = [];

function stepItems(fresh) {
  show("s-items");
  if (fresh) { picked = new Set(); others = []; level = null; hist = null; }
  const L = draft.lot;
  $("itemsWho").innerHTML = `<img class="thumb" src="/api/drafts/${draft.id}/photo?kind=marked&t=${Date.now()}" alt="">
    <div><b>Lot ${esc(lotName(L.lot))}</b> · ${esc(L.tenant_name)}</div>`;
  $("tiles").innerHTML = me.items.map((it) =>
    `<button class="tile" data-id="${it.id}" aria-pressed="${picked.has(it.id)}">${esc(it.short || it.label)}</button>`).join("") +
    `<button class="tile other" id="otherTile" aria-pressed="${others.length > 0}">${others.length ? esc(others.join(", ")) : "Something else…"}</button>`;
  for (const b of $("tiles").querySelectorAll(".tile[data-id]")) b.onclick = () => {
    picked.has(b.dataset.id) ? picked.delete(b.dataset.id) : picked.add(b.dataset.id);
    b.setAttribute("aria-pressed", String(picked.has(b.dataset.id)));
    $("itemsNext").disabled = !picked.size && !others.length;
  };
  $("otherTile").onclick = () => {
    show("s-other");
    $("other1").value = others[0] || ""; $("other2").value = others[1] || "";
    $("other1").focus();
  };
  $("itemsNext").disabled = !picked.size && !others.length;
}
$("otherDone").onclick = () => {
  others = [$("other1").value, $("other2").value].map((s) => s.trim()).filter(Boolean);
  $("other1").blur(); $("other2").blur();
  stepItems(false);
};
$("itemsNext").onclick = () => stepSend();

// ---- step 5: warning & send -----------------------------------------------------------
let hist = null;

function dueDate(days, rule, from = new Date()) {
  const d = new Date(from.getFullYear(), from.getMonth(), from.getDate() + Number(days));
  if (rule === "next_monday") d.setDate(d.getDate() + ((8 - d.getDay()) % 7));
  return d;
}

async function stepSend() {
  show("s-send");
  const L = draft.lot;
  const labels = me.items.filter((it) => picked.has(it.id)).map((it) => it.short || it.label).concat(others);
  $("sendSummary").innerHTML = `<img class="thumb" src="/api/drafts/${draft.id}/photo?kind=marked" alt="">
    <div><b>Lot ${esc(lotName(L.lot))}</b> · ${esc(L.tenant_name)}<div class="what">${esc(labels.join(" · "))}</div></div>`;
  $("levels").innerHTML = me.levels.map((l) => `<button type="button" data-l="${esc(l)}">${esc(l)}</button>`).join("");
  for (const b of $("levels").querySelectorAll("button")) b.onclick = () => { setLevel(b.dataset.l); refresh(); };
  setLevel(level); refresh();
  if (!hist) {
    $("suggestWhy").textContent = "Checking past violations…";
    const h = await api(`/api/drafts/${draft.id}/history`);
    if (h.ok) { hist = h; if (!level) setLevel(h.suggest.level); }
  }
  if (hist) $("suggestWhy").textContent = `Suggested: ${hist.suggest.level} — ${hist.suggest.why}.`;
  refresh();
}
function setLevel(l) { level = l; for (const b of $("levels").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.l === l)); }
function refresh() {
  const dates = [...picked].map((id) => { const it = me.items.find((x) => x.id === id); return dueDate(it.days, it.rule); })
    .concat(others.map(() => dueDate(me.other.days, me.other.rule)));
  const due = dates.length ? new Date(Math.max(...dates)) : null;
  $("due").textContent = due ? `Correct by ${due.toLocaleDateString([], {weekday: "long", month: "long", day: "numeric"})}` : "";
  $("issueBtn").disabled = !level || !dates.length;
  $("issueBtn").textContent = !level ? "Pick the warning" : me.mode === "live" ? "Issue violation" : "Issue violation (test)";
}

$("issueBtn").onclick = async () => {
  $("issueErr").textContent = "";
  $("issueBtn").disabled = true; $("issueBtn").textContent = "Sending…";
  const r = await api(`/api/drafts/${draft.id}/issue`, {json: {items: [...picked], others, notes: $("notes").value, warning: level}});
  if (!r.ok) { $("issueErr").textContent = r.error; refresh(); return; }
  $("notes").value = "";
  show("done"); $("doneTitle").textContent = "Writing it up…"; $("doneBody").innerHTML = ""; $("homeBtn").hidden = true;
  poll(r.job.id);
};

async function poll(id) {
  const r = await api(`/api/jobs/${id}`);
  const j = r.job;
  $("steps").innerHTML = j.steps.map((s) => `<li>${esc(s)}</li>`).join("");
  if (!j.done) { setTimeout(() => poll(id), 1000); return; }
  $("homeBtn").hidden = false;
  if (j.error) { $("doneTitle").textContent = "Not finished"; $("doneBody").innerHTML = `<div class="bad">${esc(j.error)}</div>`; return; }
  const x = j.result;
  $("doneTitle").textContent = x.test ? "Done (test)" : "Violation issued";
  $("doneBody").innerHTML = `<div class="good">Saved to ${x.test ? "the TEST record" : "the resident's History &amp; Notes"}.
    Correct by ${esc(fmtDay(x.correct_by))} — you'll get a reminder that day.
    ${x.printed ? (x.printed.ok ? " Printing now." : ` Didn't print: ${esc(x.printed.error)}`) : x.test ? " (Test mode doesn't print.)" : " No printer is set up."}</div>
    <a class="btn big full" href="${x.pdf}" target="_blank" rel="noopener">View the notice</a>`;
  draft = null;
}

window.addEventListener("resize", () => {
  if (current === "s-circle" && img?.complete) { sizeCanvas(); redraw(); }
  if (current === "home") renderActive();
});

start();
