// Violations phone app — one step at a time, one screen per step, no scrolling.
//
// Nothing waits on the manager computer while a violation is being written
// (Codi, 2026-09-25: "it is a lot of waiting to save and verify everything
// every action"). The violation stays on this phone — kept in the browser's
// own storage, so a reload doesn't lose it — and goes to the manager computer
// in ONE upload at the end, in the background (the outbox), while you carry
// on. The home screen shows each one until it's in Rent Manager. The same
// goes for "It's fixed". No AI anywhere (Codi's rule).

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const STEPS = {"s-photo": 1, "s-circle": 2, "s-lot": 3, "s-items": 4, "s-other": 4, "s-send": 5};
const TITLES = {"s-photo": "Photo", "s-circle": "Circle it", "s-lot": "Which lot?", "s-items": "What's wrong?",
                "s-other": "Something else", "s-send": "Warning & send", "s-fix": "Photo of the fix",
                "s-fixok": "It's fixed"};
const lotName = (l) => (l || "").replace(/^0+(?=\d)/, "");
const newId = () => (crypto.randomUUID ? crypto.randomUUID() :
  Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join(""));

let me = null, cur = null, park = null, parks = [];
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

// ---- the phone's own storage: the violation being written + the outbox -------
// IndexedDB when the browser allows it; memory otherwise (private browsing).
// Photos are kept as bytes, which every phone browser can store.
const store = {
  mem: new Map(), db: undefined,
  req: (r) => new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); }),
  async open() {
    if (this.db !== undefined) return this.db;
    try {
      const r = indexedDB.open("violations", 1);
      r.onupgradeneeded = () => r.result.createObjectStore("kv");
      this.db = await this.req(r);
    } catch { this.db = null; }
    return this.db;
  },
  async os(mode) { const db = await this.open(); return db && db.transaction("kv", mode).objectStore("kv"); },
  // Writes go one after another, in order, so an older copy can never land on top of a newer one.
  queue: Promise.resolve(),
  later(fn) { return (this.queue = this.queue.then(fn).catch(() => {})); },
  // photo bytes first: a storage transaction closes by itself if anything is awaited after opening it
  set(k, v) { this.mem.set(k, v); return this.later(async () => { const p = await pack(v), s = await this.os("readwrite"); if (s) await this.req(s.put(p, k)); }); },
  del(k) { this.mem.delete(k); return this.later(async () => { const s = await this.os("readwrite"); if (s) await this.req(s.delete(k)); }); },
  async all() {
    try {
      const s = await this.os("readonly");
      if (s) {
        const [keys, vals] = await Promise.all([this.req(s.getAllKeys()), this.req(s.getAll())]);
        keys.forEach((k, i) => this.mem.set(k, unpack(vals[i])));
      }
    } catch {}
    return this.mem;
  },
};
async function pack(v) { return v && v.photo instanceof Blob ? {...v, photo: {buf: await v.photo.arrayBuffer(), type: v.photo.type}} : v; }
function unpack(v) { return v && v.photo && v.photo.buf ? {...v, photo: new Blob([v.photo.buf], {type: v.photo.type})} : v; }

const saveCur = () => { if (cur) { cur.updated = Date.now(); store.set("current", cur); } };   // not awaited: never makes anyone wait

// ---- sign in -----------------------------------------------------------------
function toSignin() {
  show("signin"); $("foot").hidden = true; $("who").textContent = "";
  fetch("/api/version").then((r) => r.json()).then((v) => { $("signinVer").textContent = verText(v); }).catch(() => {});
}
// Which version this park's computer runs — so Codi can see at a glance which parks are up to date.
function verText(v) {
  if (!v || !v.version) return v && v.dev ? "Development copy" : "";
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
  const kept = await store.all();
  cur = kept.get("current") || null;
  for (const [k, o] of kept) if (k.startsWith("out:")) outbox.set(o.cid, {...o, sending: false});
  home();
  pump();
}

// ---- home: active violations, paged to fit the screen -------------------------
let active = [], page = 0;
const dueText = (d) => d > 1 ? `due in ${d} days` : d === 1 ? "due tomorrow" : d === 0 ? "due today" : `${-d} day${d === -1 ? "" : "s"} overdue`;
const dueClass = (d) => d < 0 ? "late" : d <= 1 ? "soon" : "";
const fmtDay = (iso) => new Date(iso + "T12:00").toLocaleDateString([], {weekday: "short", month: "short", day: "numeric"});

async function home() {
  show("home");
  // Back after a reload mid-violation? Go straight into it instead of asking.
  if (cur && autoResume && Date.now() - cur.updated < 3600e3) {
    autoResume = false;
    return resume();
  }
  autoResume = false;
  $("resume").innerHTML = cur ? `<div class="card resume"><b>You were in the middle of one.</b>
      <div class="row"><button class="btn primary grow" id="resumeBtn">Carry on</button><button class="btn grow" id="discardBtn">Throw it away</button></div></div>` : "";
  if (cur) {
    $("resumeBtn").onclick = resume;
    $("discardBtn").onclick = () => { cur = null; store.del("current"); home(); };
  }
  renderOutbox();
  renderActive();
  loadParks();                                   // ready for the lot step before it's needed
  let r;
  try { r = await api("/api/home"); } catch (e) {
    if (e.message !== "signin") $("active").innerHTML = `<p class="err">Couldn't reach the manager computer.</p>`;
    return;
  }
  if (!r.ok) { $("active").innerHTML = `<p class="err">${esc(r.error)}</p>`; return; }
  $("parkName").textContent = `· ${r.parks.join(" & ")}`;
  active = r.active; page = 0;
  if (current === "home") renderActive();
}

function perPage() {
  const h = $("active").clientHeight;
  return Math.max(1, Math.floor((h + 8) / (128 + 8)));
}

function renderActive() {
  // one marked fixed on this phone is off the list at once, even before it's sent
  const fixing = new Set([...outbox.values()].filter((o) => o.kind === "fix" && o.state !== "failed").map((o) => o.history_id));
  const shown = active.filter((v) => !fixing.has(v.history_id));
  if (!shown.length) {
    $("pager").hidden = true;
    $("active").innerHTML = `<p class="muted center">${$("parkName").textContent ? "No active violations." : "Loading…"}</p>`;
    return;
  }
  const n = perPage(), pages = Math.ceil(shown.length / n);
  page = Math.min(page, pages - 1);
  $("pager").hidden = pages < 2;
  $("pageText").textContent = `${page * n + 1}–${Math.min(shown.length, (page + 1) * n)} of ${shown.length}`;
  $("prevPage").disabled = page === 0; $("nextPage").disabled = page >= pages - 1;
  $("active").innerHTML = shown.slice(page * n, (page + 1) * n).map((v) => `
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

async function loadParks() {
  try { const r = await api("/api/parks"); if (r.ok) parks = r.parks; } catch {}
}

// ---- the outbox: finished violations and fixes, sent in the background --------
// States: waiting (not sent yet / no signal) → working (received, going into
// Rent Manager) → done, or failed. Each carries its own id, so sending twice
// after a dropped signal never makes a second note.
const outbox = new Map();
let pumping = false, pumpTimer = null;

function queue(o) {
  outbox.set(o.cid, o);
  store.set(`out:${o.cid}`, o);
  pump();
}

async function pump() {
  if (pumping) return;
  pumping = true;
  clearTimeout(pumpTimer);
  try {
    for (const o of [...outbox.values()]) if (o.state === "waiting") await send(o);
    for (const o of [...outbox.values()]) if (o.state === "working") await check(o);
  } finally { pumping = false; }
  renderOutbox();
  const left = [...outbox.values()];
  if (left.some((o) => o.state === "waiting" || o.state === "working")) {
    pumpTimer = setTimeout(pump, left.some((o) => o.state === "waiting") ? 15000 : 2000);
  }
}
window.addEventListener("online", pump);
document.addEventListener("visibilitychange", () => { if (!document.hidden) pump(); });

async function send(o) {
  o.error = null; o.sending = true; renderOutbox();
  try {
    if (o.photo && o.photo.size > 900e3) o.photo = await shrink(o.photo);    // e.g. a big photo from the library
    const fd = new FormData();
    const {photo, state, error, sending, label, created, thumb, steps, result, doneAt, ...form} = o;
    fd.append("form", JSON.stringify(form));
    if (photo) fd.append("photo0", photo, "photo.jpg");
    const url = o.kind === "fix" ? `/api/violations/${o.history_id}/fixed` : "/api/issue";
    const r = await fetch(url, {method: "POST", credentials: "same-origin", body: fd});
    let data = {};
    try { data = await r.json(); } catch {}
    if (r.status === 401) { o.error = "Signed out — sign in again and it will send."; return; }
    if (!r.ok || !data.ok) { finish(o, "failed", data.error || `The manager computer answered ${r.status}.`); return; }
    apply(o, data.status);
  } catch {
    o.error = "No signal — it will send by itself when there is.";
  } finally { o.sending = false; }
}

async function check(o) {
  try {
    const r = await fetch(`/api/received/${o.cid}`, {credentials: "same-origin"});
    if (r.status === 404) { o.state = "waiting"; return; }        // never arrived: send it (again)
    const data = await r.json();
    if (data.ok) apply(o, data.status);
  } catch {}
}

function apply(o, st) {
  o.steps = st.steps || [];
  if (st.state === "done") finish(o, "done", null, st.result);
  else if (st.state === "failed") finish(o, "failed", st.error);
  else { o.state = "working"; store.set(`out:${o.cid}`, o); }
}

function finish(o, state, error, result) {
  o.state = state; o.error = error; o.result = result || null; o.doneAt = Date.now();
  if (state === "done") {
    delete o.photo;
    if (o.kind === "issue") for (const k in pastViolations) delete pastViolations[k];   // the next suggestion must count it
    store.del(`out:${o.cid}`);
    setTimeout(() => { outbox.delete(o.cid); if (current === "home") renderOutbox(); }, 20000);
    if (current === "home") home();          // the list now has it (or no longer has the fixed one)
  } else {
    store.set(`out:${o.cid}`, o);
  }
}

function outLine(o) {
  const who = esc(o.label);
  if (o.state === "done") {
    const x = o.result || {};
    const extra = o.kind === "fix" ? "marked fixed" :
      x.test ? "done (test)" : `issued${x.printed ? (x.printed.ok ? " · printing" : " · didn't print") : ""}`;
    return `<div class="ob good"><span>✓ ${who} — ${extra}</span></div>`;
  }
  if (o.state === "failed") {
    return `<div class="ob bad"><span>✗ ${who} didn't save: ${esc(o.error)}</span>
      <span class="obacts"><button class="link" data-retry="${o.cid}">Try again</button>
      ${o.kind === "issue" ? `<button class="link" data-edit="${o.cid}">Change it</button>` : ""}
      <button class="link" data-drop="${o.cid}">Throw away</button></span></div>`;
  }
  const what = o.state === "working" ? (o.steps?.length ? esc(o.steps[o.steps.length - 1]) : "saving to Rent Manager…")
    : o.error ? esc(o.error) : "sending…";
  return `<div class="ob"><span>${who} — ${what}</span></div>`;
}

function renderOutbox() {
  const list = [...outbox.values()].sort((a, b) => a.created - b.created);
  $("outbox").innerHTML = list.slice(-3).map(outLine).join("");
  for (const b of $("outbox").querySelectorAll("[data-retry]")) b.onclick = () => {
    const o = outbox.get(b.dataset.retry); o.state = "waiting"; o.error = null; store.set(`out:${o.cid}`, o); pump();
  };
  for (const b of $("outbox").querySelectorAll("[data-drop]")) b.onclick = () => {
    if (!confirm("Throw this away? It is not in Rent Manager.")) return;
    outbox.delete(b.dataset.drop); store.del(`out:${b.dataset.drop}`); renderOutbox(); renderActive();
  };
  for (const b of $("outbox").querySelectorAll("[data-edit]")) b.onclick = () => {
    const o = outbox.get(b.dataset.edit);
    outbox.delete(o.cid); store.del(`out:${o.cid}`);
    const {state, error, steps, result, doneAt, sending, kind, label, created, ...v} = o;
    cur = {...v, stage: "send"}; saveCur(); resume();
  };
  if (current === "home") renderActive();       // the strip's height changes how many fit
}

// ---- starting, resuming, going back ------------------------------------------
$("addBtn").onclick = () => {
  if (cur && !confirm("Throw away the violation you were in the middle of, and start a new one?")) return;
  cur = {cid: newId(), stage: "photo", photo: null, strokes: [], lot: null,
         items: [], others: [], notes: "", warning: null, created: Date.now()};
  saveCur();
  stepPhoto();
};
$("cancelBtn").onclick = () => {
  if (!confirm("Throw away this violation? Nothing has been sent yet.")) return;
  cur = null; store.del("current");
  home();
};
$("backBtn").onclick = () => {
  if (current === "s-fix") return home();
  if (current === "s-fixok") return startFix(fixing);
  if (current === "s-send" || current === "s-other" || current === "s-items") {
    if (current === "s-items") return stepLot();
    return stepItems();
  }
  if (current === "s-lot") return stepCircle();
  if (current === "s-circle") return stepPhoto();
};

function resume() {
  const s = cur.stage;
  if (!cur.photo || s === "photo") return stepPhoto();
  if (s === "circle") return stepCircle();
  if (s === "lot" || !cur.lot) return stepLot();
  if (s === "send") return stepSend();
  return stepItems();
}

// ---- step 1: photo, with the camera inside the page -----------------------------
// Handing off to the phone's camera app made the browser reload this page on
// the way back, losing the photo.
let stream = null;

function stepPhoto() {
  if (cur) { cur.stage = "photo"; saveCur(); }
  $("photoMsg").textContent = ""; show("s-photo"); startCamera();
}

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

// The frame is copied off the camera BEFORE it is switched off, and the next
// screen shows that copy straight away — no black screen while anything saves.
async function snap(which) {
  const k = CAMS[which], v = $(k.video);
  if (!v.videoWidth) return null;
  $(k.shutter).disabled = true;
  const c = document.createElement("canvas");
  c.width = v.videoWidth; c.height = v.videoHeight;
  c.getContext("2d").drawImage(v, 0, 0);
  const blob = await new Promise((res) => c.toBlob(res, "image/jpeg", 0.9));
  c.width = c.height = 0;                        // let the phone have that memory back
  return blob;
}

$("shutter").onclick = async () => {
  const blob = await snap("violation");
  if (blob) usePhoto(blob);
};
$("camera").onchange = (e) => { if (e.target.files[0]) usePhoto(e.target.files[0]); e.target.value = ""; };
$("library").onchange = (e) => { if (e.target.files[0]) usePhoto(e.target.files[0]); e.target.value = ""; };

function usePhoto(file) {
  cur.photo = file; cur.strokes = []; cur.stage = "circle";
  stepCircle();
  // made smaller for keeping and sending, meanwhile (the marks are fractions of the photo, so they still fit)
  const mine = cur;
  shrink(file).then((small) => { if (cur === mine && cur.photo === file) { cur.photo = small; saveCur(); } });
}

async function shrink(file, edge = 2000) {
  try {
    const bmp = await createImageBitmap(file);
    const r = Math.min(1, edge / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * r); c.height = Math.round(bmp.height * r);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    bmp.close?.();
    const out = await new Promise((res) => c.toBlob(res, "image/jpeg", 0.88));
    c.width = c.height = 0;
    return out || file;
  } catch { return file; }
}

// ---- step 2: circle the problem --------------------------------------------------
let strokes = [], img = null, imgUrl = null, drawing = null;

function stepCircle() {
  cur.stage = "circle";
  show("s-circle");
  strokes = (cur.strokes || []).map((s) => s.slice());
  if (imgUrl) URL.revokeObjectURL(imgUrl);
  imgUrl = URL.createObjectURL(cur.photo);
  img = new Image();
  img.onload = () => { sizeCanvas(); redraw(); };
  img.src = imgUrl;
}

// Never bigger than the screen needs: a phone refuses (draws black) past a size.
function sizeCanvas() {
  const c = $("canvas"), wrap = $("canvasWrap");
  const r = Math.min(wrap.clientWidth / img.width, wrap.clientHeight / img.height);
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  c.style.width = `${img.width * r}px`; c.style.height = `${img.height * r}px`;
  c.width = Math.round(img.width * r * dpr); c.height = Math.round(img.height * r * dpr);
}

function drawMarks(g, w, h, list) {
  g.strokeStyle = "#e61e28"; g.lineWidth = Math.max(4, w / 110); g.lineCap = "round"; g.lineJoin = "round";
  for (const s of list) {
    g.beginPath();
    s.forEach(([x, y], i) => (i ? g.lineTo(x * w, y * h) : g.moveTo(x * w, y * h)));
    g.stroke();
  }
}

function redraw() {
  const c = $("canvas"), g = c.getContext("2d");
  g.drawImage(img, 0, 0, c.width, c.height);
  drawMarks(g, c.width, c.height, strokes.concat(drawing ? [drawing] : []));
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

// A small copy of the photo with the circle on it, for the next screens.
let thumbUrl = "";
function makeThumb() {
  const t = document.createElement("canvas"), side = 112;
  const r = Math.max(side / img.width, side / img.height);
  t.width = t.height = side;
  const g = t.getContext("2d"), w = img.width * r, h = img.height * r, x = (side - w) / 2, y = (side - h) / 2;
  g.drawImage(img, x, y, w, h);
  g.translate(x, y);
  drawMarks(g, w, h, strokes);
  thumbUrl = t.toDataURL("image/jpeg", 0.8);
}

$("circleNext").onclick = () => {
  cur.strokes = strokes.map((s) => s.map(([x, y]) => [+x.toFixed(4), +y.toFixed(4)]));
  makeThumb();
  cur.thumb = thumbUrl;
  cur.stage = "lot"; saveCur();
  stepLot();
};

// ---- step 3: lot, typed on a keypad ---------------------------------------------
let digits = "", lotPick = null, tenantPick = null;

async function stepLot() {
  cur.stage = "lot";
  show("s-lot");
  if (!parks.length) { $("lotWho").textContent = "Loading the lots…"; await loadParks(); }
  if (!parks.length) { $("lotWho").textContent = "Couldn't reach the manager computer — try again."; return; }
  if (cur.lot) park = parks.find((p) => p.property_id === cur.lot.property_id) || park;
  if (!park || !parks.includes(park)) park = parks[0];
  digits = cur.lot ? lotName(cur.lot.lot) : ""; lotPick = null; tenantPick = null;
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
  if (cur.lot && lotPick) tenantPick = lotPick.tenants.find((t) => t.id === cur.lot.tenant_id) || tenantPick;
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

// Past violations for the warning suggestion: fetched as soon as the lot is
// picked, so it's usually there before the last screen.
const pastViolations = {};          // not "history": that name is the browser's own
function historyFor(tid) {
  return pastViolations[tid] ||= api(`/api/tenants/${tid}/history`).catch(() => ({ok: false}));
}

$("lotNext").onclick = () => {
  const changed = !cur.lot || cur.lot.tenant_id !== tenantPick.id;
  cur.lot = {property_id: park.property_id, park: park.park, unit_id: lotPick.unit_id, lot: lotPick.lot,
             tenant_id: tenantPick.id, tenant_name: tenantPick.name};
  if (changed) cur.warning = null;
  cur.stage = "items"; saveCur();
  historyFor(tenantPick.id);
  stepItems();
};

// ---- step 4: what's wrong (tiles) ------------------------------------------------
function stepItems() {
  cur.stage = "items";
  show("s-items");
  const L = cur.lot, picked = new Set(cur.items);
  $("itemsWho").innerHTML = `<img class="thumb" src="${cur.thumb || ""}" alt="">
    <div><b>Lot ${esc(lotName(L.lot))}</b> · ${esc(L.tenant_name)}</div>`;
  $("tiles").innerHTML = me.items.map((it) =>
    `<button class="tile" data-id="${it.id}" aria-pressed="${picked.has(it.id)}">${esc(it.short || it.label)}</button>`).join("") +
    `<button class="tile other" id="otherTile" aria-pressed="${cur.others.length > 0}">${cur.others.length ? esc(cur.others.join(", ")) : "Something else…"}</button>`;
  for (const b of $("tiles").querySelectorAll(".tile[data-id]")) b.onclick = () => {
    const id = b.dataset.id;
    cur.items = cur.items.includes(id) ? cur.items.filter((x) => x !== id) : cur.items.concat(id);
    b.setAttribute("aria-pressed", String(cur.items.includes(id)));
    $("itemsNext").disabled = !cur.items.length && !cur.others.length;
    saveCur();
  };
  $("otherTile").onclick = () => {
    show("s-other");
    $("other1").value = cur.others[0] || ""; $("other2").value = cur.others[1] || "";
    $("other1").focus();
  };
  $("itemsNext").disabled = !cur.items.length && !cur.others.length;
}
$("otherDone").onclick = () => {
  cur.others = [$("other1").value, $("other2").value].map((s) => s.trim()).filter(Boolean);
  $("other1").blur(); $("other2").blur();
  saveCur();
  stepItems();
};
$("itemsNext").onclick = () => stepSend();

// ---- step 5: warning & send -----------------------------------------------------------
function dueDate(days, rule, from = new Date()) {
  const d = new Date(from.getFullYear(), from.getMonth(), from.getDate() + Number(days));
  if (rule === "next_monday") d.setDate(d.getDate() + ((8 - d.getDay()) % 7));
  return d;
}

async function stepSend() {
  cur.stage = "send"; saveCur();
  show("s-send");
  const L = cur.lot, mine = cur;
  const labels = me.items.filter((it) => cur.items.includes(it.id)).map((it) => it.short || it.label).concat(cur.others);
  $("sendSummary").innerHTML = `<img class="thumb" src="${cur.thumb || ""}" alt="">
    <div><b>Lot ${esc(lotName(L.lot))}</b> · ${esc(L.tenant_name)}<div class="what">${esc(labels.join(" · "))}</div></div>`;
  $("levels").innerHTML = me.levels.map((l) => `<button type="button" data-l="${esc(l)}">${esc(l)}</button>`).join("");
  for (const b of $("levels").querySelectorAll("button")) b.onclick = () => { cur.warning = b.dataset.l; saveCur(); setLevel(); refresh(); };
  $("notes").value = cur.notes || "";
  $("issueErr").textContent = "";
  setLevel(); refresh();
  $("suggestWhy").textContent = "Checking past violations…";
  const h = await historyFor(L.tenant_id);
  if (cur !== mine || current !== "s-send") return;
  if (h.ok) {
    if (!cur.warning) { cur.warning = h.suggest.level; saveCur(); setLevel(); refresh(); }
    $("suggestWhy").textContent = `Suggested: ${h.suggest.level} — ${h.suggest.why}.`;
  } else {
    $("suggestWhy").textContent = "Couldn't check past violations — pick the warning.";
  }
}
function setLevel() { for (const b of $("levels").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.l === cur.warning)); }
function refresh() {
  const dates = cur.items.map((id) => { const it = me.items.find((x) => x.id === id); return it && dueDate(it.days, it.rule); })
    .filter(Boolean).concat(cur.others.map(() => dueDate(me.other.days, me.other.rule)));
  const due = dates.length ? new Date(Math.max(...dates)) : null;
  $("due").textContent = due ? `Correct by ${due.toLocaleDateString([], {weekday: "long", month: "long", day: "numeric"})}` : "";
  $("issueBtn").disabled = !cur.warning || !dates.length;
  $("issueBtn").textContent = !cur.warning ? "Pick the warning" : me.mode === "live" ? "Issue violation" : "Issue violation (test)";
}
$("notes").oninput = () => { cur.notes = $("notes").value; saveCur(); };

// Issue = into the outbox and straight back to the list; it sends by itself.
$("issueBtn").onclick = () => {
  const L = cur.lot;
  const {stage, updated, ...v} = cur;
  queue({...v, notes: $("notes").value, kind: "issue", state: "waiting", created: Date.now(),
         property_id: L.property_id, unit_id: L.unit_id, tenant_id: L.tenant_id,
         label: `Lot ${lotName(L.lot)} · ${L.tenant_name}`});
  cur = null; store.del("current");
  home();
};

// ---- "It's fixed": photo of the fix, then (in the background) a note in History & Notes
let fixing = null, fixBlob = null, fixUrl = null;

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
  if (blob) reviewFix(blob);
};
$("fixLibrary").onchange = (e) => {
  const f = e.target.files[0]; e.target.value = "";
  if (f) reviewFix(f);
};
$("fixNoPhoto").onclick = () => reviewFix(null);

function reviewFix(blob) {
  fixBlob = blob;
  $("fixWho2").innerHTML = fixWho(fixing);
  const pic = $("fixPreview");
  if (fixUrl) URL.revokeObjectURL(fixUrl);
  fixUrl = blob ? URL.createObjectURL(blob) : null;
  pic.hidden = !blob; $("fixNoPic").hidden = !!blob;
  if (blob) pic.src = fixUrl; else pic.removeAttribute("src");
  $("fixErr").textContent = "";
  $("fixSave").disabled = false; $("fixSave").textContent = blob ? "Save — it's fixed" : "Save without a photo";
  show("s-fixok");
}
$("fixRetake").onclick = () => startFix(fixing);

$("fixSave").onclick = () => {
  queue({kind: "fix", cid: newId(), history_id: fixing.history_id, photo: fixBlob, state: "waiting",
         created: Date.now(), label: `Lot ${fixing.lot} · ${fixing.tenant_name}`});
  fixing = null; fixBlob = null;
  home();
};

window.addEventListener("resize", () => {
  if (current === "s-circle" && img?.complete) { sizeCanvas(); redraw(); }
  if (current === "home") renderActive();
});

start();
