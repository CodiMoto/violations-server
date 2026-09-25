"""A violation's photo, circle and lot, put together on the manager computer.

Since 2026-09-25 the phone keeps the whole violation to itself while it is
being written and sends it once, at the end (inbox.py) — Codi: waiting for a
save after every step was too slow. inbox.py then runs these steps in one go:
photo (shrunk, upright) → the circle burnt onto a copy → the lot and resident
→ the form violations.build() needs. The folder per violation keeps the
photos the notice and Rent Manager are made from.

No AI runs anywhere in this (Codi's rule, 2026-09-24).
"""

import io
import json
import os
import re
import secrets
import shutil
import threading
import time
from datetime import datetime

import violations as V

ROOT = os.path.join(V.DATA, "drafts")
MAX_AGE_S = 3 * 86400
_lock = threading.RLock()


def _dir(did):
    if not re.fullmatch(r"d[0-9a-f]{12}", did or ""):
        raise ValueError("Unknown draft.")
    return os.path.join(ROOT, did)


def load(did):
    try:
        with open(os.path.join(_dir(did), "draft.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        raise ValueError("That violation draft isn't there any more — start again.")


def save(d):
    d["updated"] = datetime.now().isoformat(timespec="seconds")
    path = os.path.join(_dir(d["id"]), "draft.json")
    with _lock:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, path)
    return d


def create(user):
    tidy()
    did = "d" + secrets.token_hex(6)
    os.makedirs(_dir(did))
    return save({"id": did, "user": user["username"], "stage": "photo",
                 "created": datetime.now().isoformat(timespec="seconds"),
                 "photo": False, "marks": None, "lot": None})


def open_draft(user):
    """The newest unfinished draft for this user, if any (to resume)."""
    if not os.path.isdir(ROOT):
        return None
    best = None
    for did in os.listdir(ROOT):
        try:
            d = load(did)
        except ValueError:
            continue
        if d.get("user") == user["username"] and d.get("stage") not in ("issued", "cancelled"):
            if not best or d["updated"] > best["updated"]:
                best = d
    return best


def tidy():
    """Forget drafts nobody finished within three days."""
    if not os.path.isdir(ROOT):
        return
    for did in os.listdir(ROOT):
        p = os.path.join(ROOT, did)
        if time.time() - os.path.getmtime(p) > MAX_AGE_S:
            shutil.rmtree(p, ignore_errors=True)


def photo_path(did, kind="original"):
    return os.path.join(_dir(did), f"{kind}.jpg")


# ---- step 1: the photo --------------------------------------------------------

def add_photo(did, data):
    """Keep the photo (shrunk, upright). The lot is always picked by a person:
    Codi, 2026-09-24 — AI may help design and build this program but must not
    run any part of it, so there is no machine reading of lot numbers."""
    d = load(did)
    jpeg = V.shrink_photo(data, long_edge=2000)
    with open(photo_path(did), "wb") as f:
        f.write(jpeg)
    d.update(photo=True, marks=None, stage="circle")
    return save(d)


# ---- step 2: circle the problem ---------------------------------------------

def set_marks(did, strokes):
    """`strokes` = lists of [x, y] points, 0..1 across/down the photo.
    Drawn onto a copy in red; the untouched original is kept too."""
    from PIL import Image, ImageDraw
    d = load(did)
    im = Image.open(photo_path(did)).convert("RGB")
    draw = ImageDraw.Draw(im)
    width = max(6, int(max(im.size) / 150))
    clean = []
    for s in (strokes or [])[:40]:
        pts = [(max(0.0, min(1.0, float(p[0]))), max(0.0, min(1.0, float(p[1])))) for p in s[:2000]]
        if len(pts) < 2:
            continue
        clean.append(pts)
        px = [(x * im.width, y * im.height) for x, y in pts]
        draw.line(px, fill=(230, 30, 40), width=width, joint="curve")
        for x, y in (px[0], px[-1]):
            r = width / 2
            draw.ellipse([x - r, y - r, x + r, y + r], fill=(230, 30, 40))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    with open(photo_path(did, "marked"), "wb") as f:
        f.write(buf.getvalue())
    d.update(marks=clean, stage="lot")
    return save(d)


# ---- step 3: the lot ----------------------------------------------------------

def set_lot(did, park, unit_id, tenant_id=None):
    d = load(did)
    lot = next((l for l in park["lots"] if l["unit_id"] == int(unit_id)), None)
    if not lot:
        raise ValueError("That lot isn't in Rent Manager.")
    if not lot["tenants"]:
        raise ValueError(f"Lot {lot['lot'].lstrip('0')} is vacant in Rent Manager — nobody to write it to.")
    tenant = next((t for t in lot["tenants"] if t["id"] == int(tenant_id)), None) if tenant_id \
        else lot["tenants"][0]
    d.update(stage="items", lot={"property_id": park["property_id"], "park": park["park"],
                                 "unit_id": lot["unit_id"], "lot": lot["lot"],
                                 "tenant_id": tenant["id"], "tenant_name": tenant["name"]})
    return save(d)


# ---- step 4: what's wrong, then issue ------------------------------------------

def to_form(did, items, others, notes, warning):
    """Everything violations.build() needs, from the saved draft."""
    d = load(did)
    if not d.get("photo") or not d.get("lot"):
        raise ValueError("The photo or lot is missing — go back a step.")
    photos = []
    for kind in ("marked", "original"):
        p = photo_path(did, kind)
        if os.path.exists(p):
            with open(p, "rb") as f:
                photos.append({"name": f"{kind}.jpg", "data": f.read(), "kind": kind})
    return d, {"property_id": d["lot"]["property_id"], "unit_id": d["lot"]["unit_id"],
               "tenant_id": d["lot"]["tenant_id"], "items": items or [], "others": others or [],
               "notes": notes or "", "warning": warning, "photos": photos}


def mark_issued(did, ref):
    d = load(did)
    d.update(stage="issued", ref=ref)
    return save(d)


def cancel(did):
    d = load(did)
    d["stage"] = "cancelled"
    save(d)
    shutil.rmtree(_dir(did), ignore_errors=True)
