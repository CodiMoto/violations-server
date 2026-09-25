"""Violations: phone photos + ticked checklist → the Violation Notice, saved to
the tenant's History & Notes, printed, and watched until its deadline.

Why it looks like this (researched 2026-09-23):
- Twin Peaks records violations as HISTORY NOTES on the tenant, category 3
  "Violation Notice" (1,156 since 2012), almost always with the paper form
  scanned and attached. Rent Manager's separate Violations module is unused
  (no codes set up) and the API user can't even read it (403) — so this
  follows the real practice, not the unused module.
- The paper form (see the 2026-09-09 Freeburg scans) is: park phone, lot,
  date, Reminder/1/2/Final, a two-column checklist + three "Other" lines,
  notes, "Please correct these problems by <date>", "Given under my hand this
  <Nth> day of <Month>", manager's name. The letter below reproduces it.
- Rent Manager holds no deadline anywhere, so the note carries a
  "Correct by: MM/DD/YYYY" line. The deadline watcher reads it back FROM
  RENT MANAGER — the notes are the record, this PC only remembers which
  reminders it has already sent.
"""

import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import threading
from datetime import date, datetime, timedelta

import rmconn

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "violations_config.json")
DATA = os.path.join(HERE, "data", "violations")
LOG = os.path.join(DATA, "issued.json")            # what this app issued
REMINDED = os.path.join(DATA, "reminded.json")      # HistoryID → when reminded
MOORNINGS_INBOX = os.path.join(DATA, "moornings_reminders.json")   # only Codi's computer uses it
CATEGORY_ID = 3                                      # "Violation Notice"
MARK = "[Clippy violation"                           # tags notes this app wrote
FIXED_MARK = "[Clippy violation-fixed"               # tags the "it's fixed" follow-up notes
FIXED_RE = re.compile(re.escape(FIXED_MARK) + r" (V-[\w-]+)\]")
_lock = threading.RLock()


# ---- settings -----------------------------------------------------------------

def load_config():
    if not os.path.exists(CONFIG):           # first run: start from the template
        import shutil
        shutil.copy(os.path.join(HERE, "violations_config.example.json"), CONFIG)
    with open(CONFIG, encoding="utf-8-sig") as f:     # -sig: fine if Notepad added a BOM
        cfg = json.load(f)
    with open(os.path.join(HERE, "violations_config.example.json"), encoding="utf-8-sig") as f:
        return with_defaults(cfg, json.load(f))


def with_defaults(cfg, example):
    """This computer's settings, plus anything a newer version added to the
    example that this computer doesn't have yet (updates never rewrite
    violations_config.json). Only fills gaps — never changes a setting."""
    for k, v in example.items():
        if k not in cfg:
            cfg[k] = v
        elif isinstance(v, dict) and isinstance(cfg[k], dict):
            for kk, vv in v.items():
                cfg[k].setdefault(kk, vv)
    have = {i.get("id") for i in cfg.get("items") or []}
    cfg["items"] = (cfg.get("items") or []) + [i for i in example.get("items") or [] if i.get("id") not in have]
    return cfg


def save_config(cfg):
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG)


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=1, default=str)
    os.replace(tmp, path)


# ---- passwords (phone sign-in) ------------------------------------------------

def hash_password(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()
    return f"pbkdf2${salt}${h}"


def check_password(pw, stored):
    try:
        _, salt, h = (stored or "").split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(pw, salt).split("$")[2], h)


# ---- deadlines ------------------------------------------------------------------

def item_deadline(issued, days, rule):
    """`days` after issue; 'next_monday' then moves it to the first Monday on
    or after that day, so a weekend always falls inside the time allowed."""
    d = issued + timedelta(days=int(days))
    if rule == "next_monday":
        d += timedelta(days=(7 - d.weekday()) % 7)
    return d


def deadline(issued, item_ids, others, cfg=None):
    """One date for the letter (the form has one line): the LATEST of the
    ticked items, so there's time to fix everything on it."""
    cfg = cfg or load_config()
    by_id = {i["id"]: i for i in cfg["items"]}
    dates = [item_deadline(issued, by_id[i]["days"], by_id[i]["rule"])
             for i in item_ids if i in by_id]
    dates += [item_deadline(issued, cfg["other"]["days"], cfg["other"]["rule"])
              for o in others if o.strip()]
    return max(dates) if dates else None


# ---- who lives there, and how many warnings so far ---------------------------

_lots_cache = {"at": None, "data": None}
NOT_A_VIOLATION = re.compile(r"\b(5|five|10|14|3)[ -]?day\b|\bpay(ing|ment)?\b|\blate\b|\brent\b", re.I)


def lots(conn, max_age_s=3600):
    """Every park's lots with the current tenant — what the phone picks from.
    Cached an hour; one read per park."""
    with _lock:
        if _lots_cache["data"] and (datetime.now() - _lots_cache["at"]).seconds < max_age_s:
            return _lots_cache["data"]
    props = conn.get("Properties", {"embeds": "PhoneNumbers",
                                    "fields": "PropertyID,ShortName,PhoneNumbers"}) or []
    enabled = {x.lower() for x in (load_config().get("parks") or [])}
    if enabled:        # testing one park at a time (Codi, 2026-09-24: Morristown only)
        props = [p for p in props if p["ShortName"].strip().lower() in enabled]
    out = []
    for p in sorted(props, key=lambda p: p["ShortName"]):
        # A tenant is tied to a lot through their lease, not a field on the
        # tenant. Status 2 = has an open lease. (`fields=` would silently
        # drop the Leases embed, so it isn't used here.)
        tenants = conn.rm.get_all("Tenants", filters=f"PropertyID,eq,{p['PropertyID']};Status,eq,2",
                                  embeds="Leases")
        units = conn.rm.get_all("Units", fields="UnitID,Name",
                                filters=f"PropertyID,eq,{p['PropertyID']}")
        by_unit = {}
        for t in tenants:
            for lease in t.get("Leases") or []:
                if not lease.get("MoveOutDate") and not lease.get("ActualMoveOutDate"):
                    by_unit.setdefault(lease.get("UnitID"), []).append(t)
        lot_rows = []
        for u in units:
            ts = by_unit.get(u["UnitID"], [])
            lot_rows.append({"unit_id": u["UnitID"], "lot": (u["Name"] or "").strip(),
                             "tenants": [{"id": t["TenantID"], "name": t["Name"]} for t in ts]})
        lot_rows.sort(key=lambda r: (int(re.sub(r"\D", "", r["lot"]) or 0), r["lot"]))
        phone = ((p.get("PhoneNumbers") or [{}])[0]).get("PhoneNumber", "")
        out.append({"property_id": p["PropertyID"], "park": p["ShortName"],
                    "phone": phone, "lots": lot_rows})
    with _lock:
        _lots_cache.update(at=datetime.now(), data=out)
    return out


def tenant_violations(conn, tenant_id, days=None, entity="Tenants"):
    """This tenant's violation notes, newest first — rent notices (which also
    get filed under category 3) left out."""
    cfg = load_config()
    days = days or cfg["warning_lookback_days"]
    notes = conn.get(f"{entity}/{tenant_id}/HistoryNotes") or []
    since = (datetime.now() - timedelta(days=days)).isoformat()
    out = []
    void = _read(VOID, {})
    for n in notes:
        if n.get("HistoryCategoryID") != CATEGORY_ID or (n.get("CreateDate") or "") < since:
            continue
        if str(n.get("HistoryID")) in void:          # entered in error — not a real warning
            continue
        text = n.get("Note") or ""
        if FIXED_MARK in text:                         # the "it's fixed" follow-up
            continue
        if NOT_A_VIOLATION.search(text) and MARK not in text:
            continue
        out.append({"date": (n.get("HistoryDate") or n.get("CreateDate") or "")[:10],
                    "note": re.sub(r"\s+", " ", text)[:160],
                    "warning": parse_note(text).get("warning")})
    return sorted(out, key=lambda x: x["date"], reverse=True)


def suggest_warning(history, cfg=None):
    """Next step up from the last notice this app wrote; otherwise by how many
    violation notes there have been lately. Only a suggestion."""
    levels = (cfg or load_config())["warning_levels"]
    last = next((h["warning"] for h in history if h.get("warning") in levels), None)
    if last:
        i = min(levels.index(last) + 1, len(levels) - 1)
        why = f"last notice was “{last}”"
    else:
        # Paper practice: a first notice is "1" (Reminder is a manual choice),
        # so n earlier violations → the level after n: 0→1, 1→2, 2+→Final.
        first = levels.index("1") if "1" in levels else 0
        i = min(first + len(history), len(levels) - 1)
        why = (f"{len(history)} violation note{'s' if len(history) != 1 else ''} "
               f"in the last {(cfg or load_config())['warning_lookback_days']} days")
    return {"level": levels[i], "why": why}


# ---- the note (what Rent Manager keeps) --------------------------------------

def note_text(v):
    items = "; ".join(v["item_labels"] + [f"Other: {o}" for o in v["others"] if o.strip()])
    level = v["warning"] if v["warning"] in ("Reminder", "Final") else f"Warning {v['warning']}"
    lines = [f"VIOLATION NOTICE - {level}",
             f"{v['park']} lot {v['lot']} - {v['tenant_name']}",
             items]
    if v.get("notes"):
        lines.append(f"Notes: {v['notes']}")
    lines += [f"Correct by: {v['correct_by']:%m/%d/%Y}",
              f"Issued {v['issued']:%m/%d/%Y} by {v['issued_by']} - "
              f"{len(v['photos'])} photo{'s' if len(v['photos']) != 1 else ''} attached",
              f"{MARK} {v['ref']}]"]
    return "\n".join(lines)


def parse_note(text):
    """Read back what the deadline watcher needs from a note in Rent Manager."""
    out = {}
    m = re.search(r"Correct by:\s*(\d{1,2})/(\d{1,2})/(\d{4})", text or "")
    if m:
        out["correct_by"] = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    m = re.search(r"VIOLATION NOTICE - (Reminder|Final|Warning (\d+))", text or "")
    if m:
        out["warning"] = m.group(2) or m.group(1)
    m = re.search(r"^(.+?) lot (\S+) - (.+)$", text or "", re.M)
    if m:
        out.update(park=m.group(1).strip(), lot=m.group(2), tenant_name=m.group(3).strip())
    m = re.search(r"Issued [\d/]+ by (.+?) -", text or "")
    if m:
        out["issued_by"] = m.group(1)
    m = re.search(re.escape(MARK) + r" (V-[\w-]+)\]", text or "")
    if m:
        out["ref"] = m.group(1)
    return out


# ---- the letter ---------------------------------------------------------------

def _ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def render_pdf(v, cfg=None, with_photos=True):
    """The Violation Notice, laid out like the paper form, then the photos."""
    from fpdf import FPDF
    cfg = cfg or load_config()
    pdf = FPDF(format="Letter")
    pdf.set_auto_page_break(False)
    pdf.add_page()
    W = pdf.w
    L = 22

    def center(text, size, style="", y=None, underline=False):
        if y is not None:
            pdf.set_y(y)
        pdf.set_font("Helvetica", style + ("U" if underline else ""), size)
        pdf.cell(0, size * 0.5, text, align="C", new_x="LMARGIN", new_y="NEXT")

    center(f"Park Phone: {v.get('park_phone', '')}", 12, y=16)
    pdf.ln(2)
    center("VIOLATION NOTICE (NOTICIA DE VIOLACION)", 14, "B", underline=True)
    pdf.ln(3)
    center(f"Lot: {v['lot']}        Date: {v['issued']:%m/%d/%Y}", 12)
    center(f"Resident: {v['tenant_name']}   ·   {v['park']}", 10)
    pdf.ln(2)
    center("You have been found in violation of the community rules and regulations.", 10, "B")
    center("Usted ah sido encontrado en violamiento do una de las reglas de la communidad.", 10, "BI")
    pdf.ln(3)
    center("Please correct the following items below", 10, "B")
    center("Por favor corriga las siguientes cosas se le indica abajo", 10, "BI")
    pdf.ln(4)

    def box(x, y, ticked, size=5.5):
        pdf.set_line_width(0.4)
        pdf.rect(x, y, size, size)
        if ticked:
            pdf.set_line_width(0.7)
            pdf.line(x + 1, y + 1, x + size - 1, y + size - 1)
            pdf.line(x + size - 1, y + 1, x + 1, y + size - 1)
        pdf.set_line_width(0.2)

    # Violation Number: Reminder / 1 / 2 / Final
    y = pdf.get_y()
    pdf.set_font("Helvetica", "", 12)
    pdf.set_xy(L, y); pdf.cell(38, 6, "Violation Number:")
    x = L + 40
    for lvl in cfg["warning_levels"]:
        box(x, y + 0.3, v["warning"] == lvl)
        pdf.set_xy(x + 7, y); pdf.cell(22, 6, lvl)
        x += 7 + pdf.get_string_width(lvl) + 8
    y += 13

    # two-column checklist, same split as the paper form
    items = cfg["items"]
    left, right = items[:10], items[10:]      # the paper form's split
    col_w = (W - 2 * L) / 2
    pdf.set_font("Helvetica", "", 10.5)
    for col, group in ((0, left), (1, right)):
        yy = y
        for it in group:
            x = L + col * col_w
            box(x, yy, it["id"] in v["item_ids"], 6)
            pdf.set_xy(x + 9, yy); pdf.cell(col_w - 10, 6, it["label"])
            yy += 9.5
        if col == 1:
            others = list(v["others"]) + ["", "", ""]
            for o in others[:3]:
                x = L + col * col_w
                box(x, yy, bool(o.strip()), 6)
                pdf.set_xy(x + 9, yy); pdf.cell(12, 6, "Other")
                pdf.set_font("Helvetica", "B", 10.5)
                pdf.set_xy(x + 22, yy); pdf.cell(col_w - 24, 6, o.strip()[:40])
                pdf.set_font("Helvetica", "", 10.5)
                pdf.line(x + 21, yy + 6, x + col_w - 4, yy + 6)
                yy += 9.5
    y += max(len(left), len(right) + 3) * 9.5 + 6

    pdf.set_xy(L, y)
    pdf.set_font("Helvetica", "B", 10.5); pdf.cell(16, 6, "NOTES:")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_xy(L + 17, y)
    pdf.multi_cell(W - 2 * L - 17, 6, v.get("notes") or " ")
    y = max(pdf.get_y(), y + 12) + 4
    pdf.set_xy(L, y)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(W - 2 * L, 5.5,
                   "A copy of this violation notice has been put in your permanent file. "
                   "Please correct these problems")
    pdf.set_x(L)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, f"by {v['correct_by']:%A, %B %d, %Y}.", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(L); pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, "Violations can result in eviction from the community.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.set_x(L); pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, f"Given under my hand this {_ordinal(v['issued'].day)} day of "
                   f"{v['issued']:%B, %Y}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(L); pdf.cell(0, 7, f"By {v['issued_by']}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(L); pdf.set_font("Helvetica", "B", 10); pdf.cell(0, 6, "Manager", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(L); pdf.set_font("Helvetica", "", 9); pdf.cell(0, 6, "help@twinpeaksmanagement.com")
    pdf.set_font("Helvetica", "", 7)
    pdf.set_xy(L, pdf.h - 12)
    pdf.cell(0, 4, f"Ref {v['ref']}" + (f" · {len(v['photos'])} photo(s) on the following page(s)"
                                         if with_photos and v["photos"] else ""))

    if with_photos:
        # The circled copy goes on the notice; the untouched original is
        # attached to the Rent Manager note but not printed twice.
        pics = [p for p in v["photos"] if p.get("kind") != "original"] or v["photos"]
        for i in range(0, len(pics), 2):
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_xy(L, 12)
            pdf.cell(0, 6, f"Photos - {v['park']} lot {v['lot']} - taken {v['issued']:%m/%d/%Y}")
            for k, ph in enumerate(pics[i:i + 2]):
                from PIL import Image
                im = Image.open(io.BytesIO(ph["jpeg"]))
                box_w, box_h = W - 2 * L, 118
                r = min(box_w / im.width, box_h / im.height)
                w, h = im.width * r, im.height * r
                pdf.image(io.BytesIO(ph["jpeg"]), x=L + (box_w - w) / 2, y=22 + k * 126, w=w, h=h)
    return bytes(pdf.output())


def shrink_photo(data, long_edge=2000, quality=85):
    """Phone photo → upright JPEG, capped in size (originals can be 5 MB+)."""
    from imaging import open_photo
    im = open_photo(data)
    im.thumbnail((long_edge, long_edge))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


# ---- issuing one ----------------------------------------------------------------

def build(form, user, conn, cfg=None):
    """Turn what the phone sent into a violation ready to issue."""
    cfg = cfg or load_config()
    park = next((p for p in lots(conn) if p["property_id"] == int(form["property_id"])), None)
    if not park:
        raise ValueError("Unknown park.")
    lot = next((l for l in park["lots"] if l["unit_id"] == int(form["unit_id"])), None)
    if not lot:
        raise ValueError("Unknown lot.")
    tenant = next((t for t in lot["tenants"] if t["id"] == int(form["tenant_id"])), None) \
        if form.get("tenant_id") else (lot["tenants"][0] if lot["tenants"] else None)
    if not tenant:
        raise ValueError(f"Nobody is living on {park['park']} lot {lot['lot']} in Rent Manager.")
    by_id = {i["id"]: i for i in cfg["items"]}
    item_ids = [i for i in form.get("items", []) if i in by_id]
    others = [o.strip()[:60] for o in form.get("others", []) if o.strip()][:3]
    if not item_ids and not others:
        raise ValueError("Tick at least one violation.")
    if form.get("warning") not in cfg["warning_levels"]:
        raise ValueError("Pick Reminder, 1, 2 or Final.")
    issued = date.today()
    return {
        "ref": f"V-{datetime.now():%Y%m%d-%H%M%S}",
        "mode": cfg["mode"], "issued": issued,
        "property_id": park["property_id"], "park": park["park"], "park_phone": park["phone"],
        "unit_id": lot["unit_id"], "lot": lot["lot"].lstrip("0") or lot["lot"],
        "tenant_id": tenant["id"], "tenant_name": tenant["name"],
        "item_ids": item_ids, "item_labels": [by_id[i]["label"] for i in item_ids],
        "others": others, "notes": (form.get("notes") or "").strip()[:600],
        "warning": form["warning"],
        "correct_by": deadline(issued, item_ids, others, cfg),
        "issued_by": user["name"], "issued_by_email": user.get("email"),
        "photos": form.get("photos", []),
    }


def issue(conn, v, progress=None):
    """Save the note + letter + photos to Rent Manager, print, and log it.
    In test mode everything lands on the test prospect instead of the tenant
    and nothing prints."""
    say = progress or (lambda *a: None)
    cfg = load_config()
    test = cfg["mode"] != "live"
    for ph in v["photos"]:
        ph.setdefault("jpeg", shrink_photo(ph["data"]))
    say("Making the notice…")
    pdf = render_pdf(v, cfg)
    os.makedirs(os.path.join(DATA, "letters"), exist_ok=True)
    pdf_path = os.path.join(DATA, "letters", f"{v['ref']}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(pdf)

    entity, parent = ("Prospects", cfg["test_prospect_id"]) if test else ("Tenants", v["tenant_id"])
    note = {"HistoryCategoryID": CATEGORY_ID, "HistoryDate": v["issued"].isoformat(),
            "Note": ("[TEST] " if test else "") + note_text(v),
            "HistoryAttachments": [{"File": {"MetaTag": "f0"}}] +
                                  [{"File": {"MetaTag": f"f{k + 1}"}} for k in range(len(v["photos"]))]}
    files = {"f0": (f"Violation notice {v['park']} lot {v['lot']} {v['issued']:%Y-%m-%d}.pdf",
                    pdf, "application/pdf")}
    for k, ph in enumerate(v["photos"]):
        label = {"marked": "photo (circled)", "original": "photo (original)"}.get(
            ph.get("kind"), f"photo {k + 1}")
        files[f"f{k + 1}"] = (f"{v['park']} lot {v['lot']} violation {label}.jpg",
                              ph["jpeg"], "image/jpeg")
    say("Saving to Rent Manager…" + (" (test prospect)" if test else ""))
    made = conn.write("POST", f"{entity}/{parent}/HistoryNotes",
                      params={"embeds": "HistoryAttachments,HistoryAttachments.File"},
                      data={"body": json.dumps([note])}, files=files,
                      note="Save the violation note with the notice and photos")
    row = made[0] if isinstance(made, list) and made else made or {}
    history_id = row.get("HistoryID")
    attached = len(row.get("HistoryAttachments") or [])

    printed = None
    if cfg.get("printer") and not test:
        say("Printing…")
        to_print = pdf if cfg.get("print_photos") else render_pdf(v, cfg, with_photos=False)
        printed = print_pdf(to_print, cfg["printer"], v["ref"])
    record = {k: val for k, val in v.items() if k != "photos"}
    record.update(photo_count=len(v["photos"]), history_id=history_id, attached=attached,
                  entity=entity, parent_id=parent, pdf=pdf_path, printed=printed,
                  test=test, saved_at=datetime.now().isoformat(timespec="seconds"))
    with _lock:
        log = _read(LOG, [])
        log.append(record)
        _write(LOG, log)
    _active_cache["data"] = None          # the home list should show it at once
    say("Done.")
    return record


# ---- printing -----------------------------------------------------------------

SUMATRA = os.path.join(os.path.dirname(HERE), "tools", "SumatraPDF", "SumatraPDF.exe")


def printers():
    import subprocess
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-Printer | Select-Object -ExpandProperty Name"],
                       capture_output=True, text=True, timeout=30, creationflags=0x08000000)
    return [p.strip() for p in r.stdout.splitlines() if p.strip()]


def print_pdf(pdf_bytes, printer, ref="print"):
    """Silent print through SumatraPDF (no dialog, no window)."""
    import subprocess
    path = os.path.join(DATA, "letters", f"{ref}-print.pdf")
    with open(path, "wb") as f:
        f.write(pdf_bytes)
    if not os.path.exists(SUMATRA):
        return {"ok": False, "error": "The print helper (SumatraPDF) isn't installed."}
    r = subprocess.run([SUMATRA, "-print-to", printer, "-print-settings", "fit", "-silent", path],
                       capture_output=True, text=True, timeout=120, creationflags=0x08000000)
    return {"ok": r.returncode == 0, "printer": printer,
            "error": (r.stderr or r.stdout).strip()[:300] if r.returncode else None}


# ---- the deadline watcher -----------------------------------------------------

RESOLVED = os.path.join(DATA, "resolved.json")      # HistoryID → marked fixed
VOID = os.path.join(DATA, "void.json")              # HistoryID → entered in error; never counts
_active_cache = {"at": 0, "data": None}


def active_violations(conn, parks=None, max_age_s=60):
    """Violations written by this app that are still open, soonest first —
    the phone's home screen. Read from Rent Manager (the record), cached a
    minute. Open = not marked fixed and not more than 30 days past due."""
    import time
    if _active_cache["data"] is not None and time.time() - _active_cache["at"] < max_age_s:
        rows = _active_cache["data"]
    else:
        since = (date.today() - timedelta(days=150)).isoformat()
        rows = conn.rm.get_all("HistoryNotes",
                               filters=f"HistoryCategoryID,eq,{CATEGORY_ID};CreateDate,ge,{since}",
                               fields="HistoryID,ParentID,EntityType,Note,CreateDate")
        _active_cache.update(at=time.time(), data=rows)
    resolved = _read(RESOLVED, {})
    issued = {x.get("ref"): x for x in _read(LOG, [])}
    fixed_refs = {m.group(1) for r in rows for m in [FIXED_RE.search(r.get("Note") or "")] if m}
    today = date.today()
    wanted = {p.lower() for p in (parks or [])}
    out = []
    for r in rows:
        info = parse_note(r.get("Note") or "")
        if not info.get("correct_by") or not info.get("ref"):
            continue
        if str(r["HistoryID"]) in resolved or info["ref"] in fixed_refs                 or (today - info["correct_by"]).days > 30:
            continue
        if wanted and (info.get("park") or "").lower() not in wanted:
            continue
        items = (r.get("Note") or "").splitlines()
        rec = issued.get(info["ref"], {})
        out.append({"history_id": r["HistoryID"], "park": info.get("park"), "lot": info.get("lot"),
                    "tenant_name": info.get("tenant_name"), "warning": info.get("warning"),
                    "correct_by": info["correct_by"], "days_left": (info["correct_by"] - today).days,
                    "what": items[2] if len(items) > 2 else "", "ref": info["ref"],
                    "test": (r.get("Note") or "").startswith("[TEST]"),
                    "entity": r.get("EntityType"), "parent_id": r.get("ParentID"),
                    "has_pdf": bool(rec.get("pdf") and os.path.exists(rec["pdf"]))})
    return sorted(out, key=lambda x: x["correct_by"])


def mark_void(history_id, why):
    """A note that should never have existed (e.g. a test that reached a real
    tenant): hidden everywhere and never counted towards the next warning."""
    with _lock:
        v = _read(VOID, {})
        v[str(history_id)] = {"at": datetime.now().isoformat(timespec="seconds"), "why": why}
        _write(VOID, v)
    mark_fixed(history_id, "void")


def record_fix(conn, history_id, user, photo=None):
    """"It's fixed": a follow-up note beside the original violation (same
    tenant — or the test record for a test notice), with the photo of the
    fix attached, then off the active list. The note carries the original's
    reference so Rent Manager itself records that it was closed."""
    orig = conn.get(f"HistoryNotes/{history_id}",
                    {"fields": "HistoryID,ParentID,EntityType,Note,HistoryCategoryID"})
    info = parse_note((orig or {}).get("Note") or "")
    if not orig or not info.get("ref"):
        raise ValueError("That isn't a violation written by this app.")
    entity = {"Tenant": "Tenants", "Prospect": "Prospects"}.get(orig.get("EntityType"))
    if not entity:
        raise ValueError("Can't tell whose record that violation is on.")
    lines = (orig.get("Note") or "").splitlines()
    test = (orig.get("Note") or "").startswith("[TEST]")
    what = lines[2] if len(lines) > 2 else ""
    today = date.today()
    text = "\n".join([
        ("[TEST] " if test else "") + "VIOLATION FIXED",
        f"{info.get('park')} lot {info.get('lot')} - {info.get('tenant_name')}",
        what,
        f"Checked {today:%m/%d/%Y} by {user['name']} - fixed"
        + (f" (deadline was {info['correct_by']:%m/%d/%Y})" if info.get("correct_by") else "")
        + (" - photo attached" if photo else " - no photo"),
        f"{FIXED_MARK} {info['ref']}]"])
    note = {"HistoryCategoryID": CATEGORY_ID, "HistoryDate": today.isoformat(), "Note": text}
    if photo:
        note["HistoryAttachments"] = [{"File": {"MetaTag": "f0"}}]
        made = conn.write("POST", f"{entity}/{orig['ParentID']}/HistoryNotes",
                          params={"embeds": "HistoryAttachments,HistoryAttachments.File"},
                          data={"body": json.dumps([note])},
                          files={"f0": (f"{info.get('park')} lot {info.get('lot')} fixed {today:%Y-%m-%d}.jpg",
                                        shrink_photo(photo), "image/jpeg")},
                          note="Save the 'fixed' note with the photo")
    else:
        made = conn.write("POST", f"{entity}/{orig['ParentID']}/HistoryNotes", json_body=[note],
                          note="Save the 'fixed' note")
    row = made[0] if isinstance(made, list) and made else made or {}
    mark_fixed(history_id, user["username"])
    return {"history_id": row.get("HistoryID"), "attached": len(row.get("HistoryAttachments") or []),
            "test": test, "lot": info.get("lot"), "tenant_name": info.get("tenant_name")}


def mark_fixed(history_id, by):
    with _lock:
        res = _read(RESOLVED, {})
        res[str(history_id)] = {"at": datetime.now().isoformat(timespec="seconds"), "by": by}
        _write(RESOLVED, res)
    _active_cache["data"] = None


def due_violations(conn, today=None, lookback_days=200):
    """Violation notes in Rent Manager whose "Correct by" date has arrived and
    that haven't had a reminder yet. Reads Rent Manager, not a local list, so
    the notes stay the record."""
    today = today or date.today()
    since = (today - timedelta(days=lookback_days)).isoformat()
    rows = conn.rm.get_all("HistoryNotes",
                           filters=f"HistoryCategoryID,eq,{CATEGORY_ID};CreateDate,ge,{since}",
                           fields="HistoryID,ParentID,EntityType,Note,CreateDate,CreateUserID")
    reminded = _read(REMINDED, {})
    out = []
    for r in rows:
        info = parse_note(r.get("Note") or "")
        if not info.get("correct_by") or info["correct_by"] > today:
            continue
        if str(r["HistoryID"]) in reminded or str(r["HistoryID"]) in _read(RESOLVED, {}):
            continue
        if info.get("ref") and any(FIXED_MARK + " " + info["ref"] + "]" in (x.get("Note") or "") for x in rows):
            continue
        info.update(history_id=r["HistoryID"], parent_id=r["ParentID"],
                    entity=r.get("EntityType"), note=r.get("Note"),
                    test=(r.get("Note") or "").startswith("[TEST]"))
        out.append(info)
    return out


def remind(conn, send=True, today=None):
    """Send every reminder that's due: email to Codi, email to whoever wrote
    it, and a line on Moornings' list. Marks each so it's sent once."""
    import notify
    cfg = load_config()
    rcfg = cfg["reminders"]
    hour = datetime.now().hour
    if send and not (rcfg["send_from_hour"] <= hour < rcfg["send_until_hour"]):
        return {"skipped": "outside reminder hours"}
    log = {x["ref"]: x for x in _read(LOG, []) if x.get("ref")}
    users = {u["name"]: u for u in cfg["users"]}
    done = []
    for d in due_violations(conn, today=today):
        issued = log.get(d.get("ref"), {})
        to = {rcfg["email_me"]}
        writer = users.get(d.get("issued_by"))
        if rcfg.get("email_writer") and writer and writer.get("email"):
            to.add(writer["email"])
        subject = (("[TEST] " if d["test"] else "") +
                   f"Violation deadline: {d.get('park')} lot {d.get('lot')} — "
                   f"re-check today ({d['correct_by']:%a %m/%d})")
        body = (f"The violation notice for {d.get('tenant_name')} at {d.get('park')} lot "
                f"{d.get('lot')} said to correct it by {d['correct_by']:%A, %B %d}.\n\n"
                f"Time to go and look. If it's fixed, nothing else to do. If not, issue the "
                f"next notice from the phone app (it will suggest the next warning level).\n\n"
                f"What the notice said:\n{d['note']}\n")
        attach = [(os.path.basename(issued["pdf"]), open(issued["pdf"], "rb").read())] \
            if issued.get("pdf") and os.path.exists(issued["pdf"]) else []
        result = {"history_id": d["history_id"], "to": sorted(to), "subject": subject}
        if send:
            # Manager computers have no email set up (Codi, 2026-09-24): the
            # phone app's home screen shows the overdue ones instead.
            if rcfg.get("email") and notify.configured():
                result["email"] = [notify.send(addr, subject, body, attach) for addr in sorted(to)]
            if rcfg.get("moornings"):
                add_moornings_item(d)
            with _lock:
                rem = _read(REMINDED, {})
                rem[str(d["history_id"])] = datetime.now().isoformat(timespec="seconds")
                _write(REMINDED, rem)
        done.append(result)
    return {"reminders": done}


def add_moornings_item(d):
    """Queue a line for Moornings' "My own list". Moornings picks these up
    itself (Api.lists) so its own save can never race with this file. Only on
    Codi's computer (reminders.moornings in violations_config.json)."""
    with _lock:
        items = _read(MOORNINGS_INBOX, [])
        iid = f"viol-{d['history_id']}"
        if not any(i["id"] == iid for i in items):
            items.append({"id": iid,
                          "text": (("[TEST] " if d.get("test") else "") +
                                   f"Re-check {d.get('park')} lot {d.get('lot')} violation "
                                   f"(due {d['correct_by']:%m/%d})"),
                          "note": f"{d.get('tenant_name')} — notice from Rent Manager tools",
                          "added_at": datetime.now().astimezone().isoformat(),
                          "delivered": False})
            _write(MOORNINGS_INBOX, items)
