"""Late notices: when Rent Manager posts the month's late fees, print a late
notice and a ledger for every tenant it charged one, and note it on their account.

Both documents are made by Rent Manager itself, so they match what the office
prints by hand:
  - the notice is this park's letter template (e.g. "14 day late notice (MN)"),
    merged by Rent Manager for the tenant;
  - the ledger is Rent Manager's "Statement - 8.5x11" report for the tenant,
    starting the day after the account was last at a zero (or credit) balance.

Who gets one: tenants Rent Manager charged a late fee (charge type LC) this
month — so its own late-fee rules and exemptions decide (Codi, 2026-10-08).
A tenant who has paid in full by the time it runs is skipped.

When: the server checks every 15 minutes. Once this month's late fees are in
and two checks in a row see the same ones (the posting run is finished), it
prints the notices in lot order: notice, then ledger. Each tenant once a month.

The notice text is state law, so a park only gets this when its letter
template is chosen in violations_config.json (late_notices.letter_template_id).
Off everywhere else. Test mode prints nothing and writes nothing.
"""

import json
import os
import re
import time
from datetime import date, datetime, timedelta

import rmconn
import violations as V

STATE = os.path.join(V.HERE, "data", "late", "state.json")
OUT = os.path.join(V.HERE, "data", "late", "pdf")
MARK = "[Clippy late notice"                        # tags the notes this writes


def settings(cfg=None):
    return (cfg or V.load_config()).get("late_notices") or {}


# ---- the ledger's start date ---------------------------------------------------

def ledger_start(transactions, balance):
    """The day after the account was last at a zero or credit balance, and whether
    the transactions add up to Rent Manager's balance (they must — else the ledger
    would not explain the amount due). Payments and credits come with negative
    amounts, so the balance is their plain sum."""
    by_day = {}
    for t in transactions:
        d = (t.get("TransactionDate") or "")[:10]
        by_day[d] = by_day.get(d, 0.0) + (t.get("Amount") or 0.0)
    run, last_zero, first = 0.0, None, None
    for d in sorted(by_day):
        first = first or d
        run = round(run + by_day[d], 2)
        if run <= 0.005:
            last_zero = d
    start = (date.fromisoformat(last_zero) + timedelta(days=1)) if last_zero else \
        (date.fromisoformat(first) if first else date.today())
    return start, round(run, 2), abs(run - (balance or 0)) < 0.005


# ---- which tenants, and when ---------------------------------------------------

def fingerprint(charges):
    """Which late charges exist — to tell when Rent Manager has finished posting them."""
    return sorted(c["ChargeID"] for c in charges)


def due(charges, done, seen_before):
    """From this month's late charges: tenant IDs still to do, or None while Rent
    Manager may still be posting them. Finished = the same late charges as at the
    previous check (15 minutes earlier). Rent Manager's own timestamps are in its
    server's time zone, so they can't be compared with this computer's clock."""
    if not charges:
        return []
    if fingerprint(charges) != seen_before:
        return None
    ids = []
    for c in charges:
        tid = c.get("AccountID")
        if c.get("AccountType") == "Customer" and tid not in ids and str(tid) not in done:
            ids.append(tid)
    return ids


def _lot_order(lot):
    return int(re.sub(r"\D", "", lot or "") or 0), lot or ""


# ---- Rent Manager's own documents ---------------------------------------------

def _get(conn, path, params, tries=3):
    """Rent Manager sometimes trips over its own temp file ("being used by another
    process") while making a PDF — that is worth one or two more tries."""
    for n in range(1, tries + 1):
        try:
            return conn.rm.request("GET", path, params=params)
        except rmconn.rmclient.RentManagerError as e:
            if n == tries or "being used by another process" not in str(getattr(e, "body", e)):
                raise
            time.sleep(5 * n)


def notice_pdf(conn, template_id, tenant_id):
    """The letter template merged by Rent Manager for this tenant, as a PDF. (Asking
    for the PDF itself fails on Rent Manager's side; the download link works.)"""
    url, _ = _get(conn, f"LetterTemplates/{template_id}/MergeLetterTemplate",
                  {"recipientIDs": tenant_id, "getOptions": "ReturnPDFUrl"})
    url = str(url).strip().strip('"')
    for n in range(1, 4):
        r = conn.rm.session.get(url, timeout=120)
        if r.content[:4] == b"%PDF":
            return r.content
        time.sleep(3 * n)
    raise RuntimeError(f"Rent Manager didn't return the letter as a PDF ({r.status_code}).")


def ledger_pdf(conn, report_id, property_id, tenant_id, start, end):
    params = {"StartDate": f"{start:%Y/%m/%d}", "EndDate": f"{end:%Y/%m/%d}", "PropertyIDs": str(property_id),
              "CUSTIDS": str(tenant_id), "CustomerStatus": "2", "SORTOPTIONS": "Unit"}
    q = {"getOptions": "ReturnPDFStream"}
    for i, (k, v) in enumerate(params.items()):
        q[f"parameters[{i}].Parameter"] = k
        q[f"parameters[{i}].Value"] = v
    _, resp = _get(conn, f"Reports/{report_id}/RunReport", q)
    if resp.content[:4] != b"%PDF":
        raise RuntimeError(f"Rent Manager didn't return the ledger as a PDF ({resp.status_code}).")
    return resp.content


# ---- one tenant ------------------------------------------------------------------

def note_text(month, template_name, balance, start, printed):
    how = "printed" if printed else "made (it didn't print — see the attached copies)"
    return (f"{template_name} {how} {date.today():%m/%d/%Y}. Total due ${balance:,.2f}; "
            f"ledger from {start:%m/%d/%Y}.\n{MARK} {month}]")


def one(conn, cfg, s, month, tenant_id, property_id, lot, template_name, say=print):
    """Notice + ledger for one tenant: make, print (live), note on the account (live)."""
    live = cfg["mode"] == "live"
    t = conn.get(f"Tenants/{tenant_id}", {"embeds": "Balance", "fields": "TenantID,Name,Balance"}) or {}
    balance = t.get("Balance") or 0.0
    if balance <= 0:
        return {"state": "skipped", "why": f"paid (balance {balance:.2f})", "lot": lot}
    tx = conn.rm.get_all(f"Tenants/{tenant_id}/Transactions")
    start, total, adds_up = ledger_start(tx, balance)
    if not adds_up:
        return {"state": "failed", "lot": lot,
                "why": f"transactions add up to {total:.2f}, Rent Manager says {balance:.2f} — not sent"}
    notice = notice_pdf(conn, s["letter_template_id"], tenant_id)
    ledger = ledger_pdf(conn, s.get("statement_report_id", 284), property_id, tenant_id, start, date.today())
    ref = f"LATE-{month}-{_lot_order(lot)[0]:04d}-{tenant_id}"
    os.makedirs(OUT, exist_ok=True)
    for kind, pdf in (("notice", notice), ("ledger", ledger)):
        with open(os.path.join(OUT, f"{ref}-{kind}.pdf"), "wb") as f:
            f.write(pdf)
    rec = {"state": "done", "lot": lot, "balance": balance, "ledger_from": str(start), "ref": ref,
           "test": not live, "at": datetime.now().isoformat(timespec="seconds")}
    if not live:
        say(f"  lot {lot}: TEST - made the notice and ledger (${balance:,.2f} due), printed nothing")
        return rec
    printed = []
    if cfg.get("printer"):
        for n, (kind, pdf) in enumerate((("notice", notice), ("ledger", ledger)), 1):
            printed.append(V.print_pdf(pdf, cfg["printer"], f"{ref}-{n}-{kind}"))
    ok = bool(printed) and all(p.get("ok") for p in printed)
    note = {"HistoryCategoryID": s.get("history_category_id", 4), "HistoryDate": date.today().isoformat(),
            "Note": note_text(month, template_name, balance, start, ok),
            "HistoryAttachments": [{"File": {"MetaTag": "f0"}}, {"File": {"MetaTag": "f1"}}]}
    files = {"f0": (f"Late notice lot {lot} {date.today():%Y-%m-%d}.pdf", notice, "application/pdf"),
             "f1": (f"Ledger lot {lot} {date.today():%Y-%m-%d}.pdf", ledger, "application/pdf")}
    made = conn.write("POST", f"Tenants/{tenant_id}/HistoryNotes",
                      params={"embeds": "HistoryAttachments,HistoryAttachments.File"},
                      data={"body": json.dumps([note])}, files=files, note="Note the late notice on the account")
    row = made[0] if isinstance(made, list) and made else made or {}
    rec.update(printed=printed, history_id=row.get("HistoryID"))
    say(f"  lot {lot}: ${balance:,.2f} due, ledger from {start:%m/%d}, "
        f"{'printed' if ok else 'NOT printed'}, note {row.get('HistoryID')}")
    return rec


# ---- the monthly run -------------------------------------------------------------

def check(conn=None, say=print, now=None):
    """Run by the phone server every 15 minutes. Returns what it did."""
    cfg = V.load_config()
    s = settings(cfg)
    if not s.get("letter_template_id"):
        return {"result": "off (no letter template chosen for this park)"}
    now = now or datetime.now()
    month = f"{now:%Y-%m}"
    state = V._read(STATE, {})
    # Test runs are kept apart, so going live later in the month still prints everyone.
    done = state.setdefault(month if cfg["mode"] == "live" else f"{month} test", {})
    conn = conn or rmconn.Connection("live" if cfg["mode"] == "live" else "practice")

    props = conn.get("Properties", {"fields": "PropertyID,ShortName"}) or []
    parks = {x.lower() for x in (cfg.get("parks") or [])}
    props = {p["PropertyID"]: p["ShortName"].strip() for p in props
             if not parks or p["ShortName"].strip().lower() in parks}
    charges = [c for c in conn.rm.get_all(
        "Charges", filters=f"ChargeTypeID,eq,{s.get('late_charge_type_id', 10)};"
                           f"TransactionDate,ge,{now:%Y-%m}-01",
        fields="ChargeID,AccountID,AccountType,PropertyID,UnitID,Amount,CreateDate")
        if c.get("PropertyID") in props]
    seen = state.setdefault("seen", {})
    todo = due(charges, done, seen.get(month))
    if todo is None:
        seen[month] = fingerprint(charges)
        V._write(STATE, state)
        return {"result": f"{len(charges)} late fee(s) posted - printing at the next check if no more are added"}
    if not todo:
        return {"result": f"nothing to do ({len(done)} done this month)"}

    units = {}
    for pid in {c["PropertyID"] for c in charges}:
        for u in conn.rm.get_all("Units", fields="UnitID,Name", filters=f"PropertyID,eq,{pid}"):
            units[u["UnitID"]] = (u.get("Name") or "").strip()
    first = {}
    for c in charges:
        first.setdefault(c["AccountID"], c)
    todo.sort(key=lambda tid: (props.get(first[tid]["PropertyID"], ""), _lot_order(units.get(first[tid]["UnitID"]))))
    template = conn.get(f"LetterTemplates/{s['letter_template_id']}", {"fields": "Name"}) or {}
    name = template.get("Name") or "Late notice"

    say(f"late notices {month}: {len(todo)} tenant(s) - {name}")
    did = []
    for tid in todo:
        c = first[tid]
        lot = units.get(c["UnitID"], "?")
        try:
            rec = one(conn, cfg, s, month, tid, c["PropertyID"], lot, name, say)
        except Exception as e:
            rec = {"state": "failed", "lot": lot, "why": rmconn._msg(e) if hasattr(e, "body") else str(e)}
            say(f"  lot {lot}: FAILED - {rec['why']}")
        if rec["state"] == "failed":
            continue                      # tried again on the next check
        done[str(tid)] = rec
        V._write(STATE, state)            # saved after each one: a restart never prints twice
        did.append(rec)
    return {"result": f"{len(did)} of {len(todo)} done", "done": did}


def status(month=None):
    month = month or f"{date.today():%Y-%m}"
    return V._read(STATE, {}).get(month, {})
