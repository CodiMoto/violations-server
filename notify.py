"""Reminder emails, sent as Codi through her Gmail sign-in.

Kept separate from Moornings' manager_send.py on purpose: that module only
ever sends when Codi presses Send. This one sends automatically, so it is
fenced in instead — it will only ever write to an address listed in
violations_config.json (the reminder address or a signed-in user), and only
reminder text built by violations.remind().
"""
import base64
import os
from email.message import EmailMessage

HERE = os.path.dirname(os.path.abspath(__file__))
def _token():
    """Where this computer's Gmail sign-in is (violations_config.json →
    reminders.gmail_token). Only Codi's computer has one."""
    import violations
    p = (violations.load_config().get("reminders") or {}).get("gmail_token")
    if not p:
        return None
    p = p if os.path.isabs(p) else os.path.normpath(os.path.join(HERE, p))
    return p if os.path.exists(p) else None


def configured():
    return _token() is not None
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def allowed():
    import violations
    cfg = violations.load_config()
    return {cfg["reminders"]["email_me"].lower()} | \
        {u["email"].lower() for u in cfg["users"] if u.get("email")}


def send(to, subject, body, attachments=()):
    if not configured():
        return {"ok": False, "error": "email isn't set up on this computer"}
    if to.lower() not in allowed():
        return {"ok": False, "error": f"{to} isn't a configured reminder address"}
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = "me"
    msg["Subject"] = subject
    msg.set_content(body)
    for name, data in attachments:
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    try:
        creds = Credentials.from_authorized_user_file(_token(), SCOPES)
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        r = svc.users().messages().send(
            userId="me", body={"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}).execute()
        return {"ok": True, "to": to, "id": r.get("id")}
    except Exception as e:
        return {"ok": False, "to": to, "error": str(e)[:300]}
