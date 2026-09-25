"""What the phone sends — each violation and each "it's fixed" — received once.

Codi, 2026-09-25: saving and checking at every step made writing a violation
slow. Now the phone keeps the whole violation to itself (photo, circle, lot,
what's wrong, warning) and sends it in ONE upload at the end, in the
background, while the manager carries on. So:

  * every send carries the phone's own id for it (cid). If the signal drops
    and the phone sends it again, the second copy is recognised and never
    makes a second note in Rent Manager;
  * the upload is answered as soon as it has been checked (the lot, the
    items, the warning); the Rent Manager note, the notice and the printing
    happen here afterwards, one violation at a time;
  * the phone asks /api/received/<cid> how it went, and shows that on its
    home screen.

received.json keeps what happened to each cid for 30 days.
"""

import os
import re
import threading
import traceback
from datetime import datetime, timedelta

import drafts
import rmconn
import violations as V

FILE = os.path.join(V.DATA, "received.json")
KEEP_DAYS = 30
CID = re.compile(r"[A-Za-z0-9_-]{8,64}")

_lock = threading.RLock()
_issue_lock = threading.Lock()          # one violation into Rent Manager at a time
_steps = {}                              # cid → progress lines while it's being saved
log = lambda msg: None                   # mgrserver sets its own


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _all():
    return V._read(FILE, {})


def _put(cid, **fields):
    with _lock:
        every = _all()
        every[cid] = {**every.get(cid, {}), **fields, "cid": cid, "updated": _now()}
        cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).isoformat()
        every = {k: e for k, e in every.items() if e.get("updated", "") >= cutoff}
        V._write(FILE, every)
        return every[cid]


def _check_cid(cid):
    if not CID.fullmatch(str(cid or "")):
        raise ValueError("The phone didn't send an id for this — reload the app and try again.")
    return cid


def status(cid, user):
    e = _all().get(cid)
    if not e or e.get("user") != user["username"]:
        return None
    return {k: e.get(k) for k in ("cid", "kind", "state", "ref", "park", "lot", "tenant_name",
                                  "result", "error", "received", "updated")} | \
        {"steps": list(_steps.get(cid, e.get("steps") or []))}


# ---- a violation --------------------------------------------------------------

def submit_violation(user, form, photos, park):
    """Check it, keep it, answer at once; Rent Manager and printing follow in
    the background. A copy already received is not issued again."""
    cid = _check_cid(form.get("cid"))
    with _lock:
        prev = _all().get(cid)
        if prev and prev.get("state") in ("working", "done"):
            return status(cid, user)
        if isinstance(photos, (bytes, bytearray)):
            photos = [photos]
        photos = [p for p in photos or [] if p]
        if not photos:
            raise ValueError("No photo came through — try again.")
        # marks: one list of strokes per photo (older phones sent "strokes" for a single photo)
        marks = form.get("marks") or [form.get("strokes") or []]
        d = drafts.create(user)
        for n, data in enumerate(photos[:drafts.MAX_PHOTOS]):
            drafts.add_photo(d["id"], data, n)
            drafts.set_marks(d["id"], marks[n] if n < len(marks) else [], n)
        drafts.set_lot(d["id"], park, form["unit_id"], form.get("tenant_id"))
        _, f = drafts.to_form(d["id"], form.get("items"), form.get("others"), form.get("notes"),
                              form.get("warning"))
        v = V.build(f, user, rmconn.Connection("practice"))
        used = {e.get("ref") for e in _all().values()}
        if v["ref"] in used:                   # two sent in the same second
            v["ref"] = f"{v['ref']}-{cid[:4]}"
        _put(cid, kind="issue", user=user["username"], state="working", ref=v["ref"],
             park=v["park"], lot=v["lot"], tenant_name=v["tenant_name"], received=_now(),
             draft=d["id"], error=None, result=None, steps=[])
    _steps[cid] = []
    threading.Thread(target=_issue, args=(cid, v, d["id"]), daemon=True).start()
    log(f"{user['username']} sent {v['ref']} ({cid}): {v['park']} lot {v['lot']}")
    return status(cid, user)


def _issue(cid, v, did):
    steps = _steps.setdefault(cid, [])
    with _issue_lock:
        try:
            rec = V.issue(rmconn.Connection("live"), v, progress=steps.append)
            drafts.mark_issued(did, rec["ref"])
            _put(cid, state="done", steps=steps, result={
                "ref": rec["ref"], "history_id": rec["history_id"], "attached": rec["attached"],
                "correct_by": rec["correct_by"], "printed": rec["printed"], "test": rec["test"],
                "pdf": f"/api/letters/{rec['ref']}.pdf"})
        except Exception as e:
            _put(cid, state="failed", steps=steps,
                 error=rmconn._msg(e) if hasattr(e, "body") else str(e))
            log(f"{v['ref']} ({cid}) failed\n" + traceback.format_exc())
        finally:
            _steps.pop(cid, None)


# ---- "it's fixed" -------------------------------------------------------------

def submit_fix(user, cid, history_id, photo, conn):
    """The "fixed" note (with the photo) — once, however often it's sent."""
    cid = _check_cid(cid)
    with _lock:
        prev = _all().get(cid)
        if prev and prev.get("state") == "done":
            return status(cid, user)
        _put(cid, kind="fix", user=user["username"], state="working", received=_now(),
             history_id=history_id, error=None)
    try:
        res = V.record_fix(conn, history_id, user, photo)
    except Exception as e:
        _put(cid, state="failed", error=rmconn._msg(e) if hasattr(e, "body") else str(e))
        raise
    _put(cid, state="done", result=res)
    return status(cid, user)


# ---- after a restart ------------------------------------------------------------

def recover():
    """Anything still "working" when the server stopped: done if it reached
    the issued log, otherwise failed — with a warning to look first, since
    Rent Manager may already have the note."""
    with _lock:
        every = _all()
        issued = {r.get("ref"): r for r in V._read(V.LOG, [])}
        changed = False
        for cid, e in every.items():
            if e.get("state") != "working":
                continue
            rec = issued.get(e.get("ref"))
            if rec:
                e.update(state="done", result={k: rec.get(k) for k in (
                    "ref", "history_id", "correct_by", "printed", "test")} |
                    {"pdf": f"/api/letters/{rec.get('ref')}.pdf"})
            else:
                e.update(state="failed", error="The manager computer restarted part-way through. Check the "
                                               "resident's History & Notes before sending it again.")
            changed = True
        if changed:
            V._write(FILE, every)
