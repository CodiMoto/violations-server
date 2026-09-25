"""The manager server: what the phone talks to.

Runs on the manager's computer (developed on Codi's), always on, started at
sign-in by the "Violations Server" task (install.ps1). It serves the
phone app (ui/phone/), takes in violations with their photos, hands them to
violations.py to write up, save to Rent Manager and print, and every half
hour checks Rent Manager for violation deadlines that have arrived.

Reached from anywhere through Tailscale Funnel (only this PC runs
Tailscale; the phone just uses its browser). That makes the sign-in page
reachable from the internet, so: it listens on 127.0.0.1 only (Funnel
forwards to it), every API call needs a signed-in session, wrong passwords
are rate-limited per address and per username (with an email to Codi when a
username gets locked), passwords must be 12+ characters, setting a new
password signs out every phone, and pages carry strict browser security
headers. Sign-in: username + password set on the Violations screen (stored
as a salted hash in violations_config.json).

  pythonw mgrserver.py             the real thing
  python  mgrserver.py --lan       also reachable on the home network
"""

import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
import traceback
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_default
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import rmconn  # noqa: E402
import updater  # noqa: E402
import violations as V  # noqa: E402
import inbox  # noqa: E402

PHONE_UI = os.path.join(HERE, "ui", "phone")
SECRET_FILE = os.path.join(V.DATA, "session_secret")
SERVER_LOG = os.path.join(V.DATA, "server.log")
MAX_UPLOAD = 80 * 1024 * 1024          # a dozen full-size phone photos
SESSION_DAYS = 30

_fails = {}                            # ip → [times] of failed sign-ins


def log(msg):
    os.makedirs(V.DATA, exist_ok=True)
    try:
        with open(SERVER_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass


def _touch_activity(_last=[0.0]):
    """Tells updater.py the phone app is in use (it waits for 10 quiet minutes)."""
    if time.time() - _last[0] > 30:
        _last[0] = time.time()
        try:
            os.makedirs(V.DATA, exist_ok=True)
            with open(os.path.join(V.DATA, "last_activity"), "w") as f:
                f.write(datetime.now().isoformat(timespec="seconds"))
        except OSError:
            pass


def _secret():
    os.makedirs(V.DATA, exist_ok=True)
    if not os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "w") as f:
            f.write(secrets.token_hex(32))
    with open(SECRET_FILE) as f:
        return f.read().strip().encode()


def _user(username):
    return next((u for u in V.load_config()["users"] if u["username"] == username), None)


def _sign(username, exp):
    # The stored password hash is part of the signature, so setting a new
    # password signs out every phone that was signed in with the old one.
    u = _user(username) or {}
    key = _secret() + (u.get("password_hash") or "").encode()
    return hmac.new(key, f"{username}|{exp}".encode(), hashlib.sha256).hexdigest()


def make_session(username):
    exp = int(time.time()) + SESSION_DAYS * 86400
    return f"{username}|{exp}|{_sign(username, exp)}"


def read_session(cookie):
    try:
        username, exp, sig = cookie.split("|")
        u = _user(username)
        if u and u.get("password_hash") and int(exp) > time.time() and \
                hmac.compare_digest(sig, _sign(username, exp)):
            return u
    except (ValueError, AttributeError):
        pass
    return None


# ---- guarding the sign-in (the page is reachable from the internet) --------
IP_LIMIT, IP_WINDOW = 8, 900          # 8 wrong tries per address per 15 min
USER_LIMIT, USER_WINDOW = 20, 3600    # 20 wrong tries on one username per hour
_user_fails = {}
_alerted = {}


def _too_many(bucket, key, limit, window):
    recent = [t for t in bucket.get(key, []) if time.time() - t < window]
    bucket[key] = recent
    return len(recent) >= limit


def _alert(username, ip):
    """Email Codi (once an hour at most) when someone is guessing passwords."""
    if time.time() - _alerted.get(username, 0) < 3600:
        return
    _alerted[username] = time.time()
    try:
        import notify
        cfg = V.load_config()
        notify.send(cfg["reminders"]["email_me"],
                    "Violations app: someone is guessing passwords",
                    f"There have been {USER_LIMIT}+ wrong passwords for the username "
                    f"'{username}' in the last hour (latest from {ip}). Sign-in for that "
                    f"username is paused for an hour.\n\nIf this wasn't you, set a new "
                    f"password in Violations Settings — that also "
                    f"signs out every phone.")
    except Exception:
        log("could not send the lockout alert\n" + traceback.format_exc())


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=PHONE_UI, **k)

    server_version = "mgr"
    sys_version = ""

    def log_message(self, *args):
        pass

    def client_ip(self):
        """Behind Tailscale Funnel every request comes from this PC itself;
        the real address is in X-Forwarded-For."""
        peer = self.client_address[0]
        fwd = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return fwd if peer in ("127.0.0.1", "::1") and fwd else peer

    def https(self):
        return (self.headers.get("X-Forwarded-Proto") == "https"
                or (self.headers.get("Host") or "").endswith(".ts.net"))

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; "
                         "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                         "base-uri 'none'; form-action 'self'; object-src 'none'")
        if self.https():
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404)          # never show a folder listing

    # ---- helpers ----
    def user(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "vsess":
                return read_session(v)
        return None

    def reply(self, obj, status=200, headers=None):
        body = json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("Too much to upload at once — send fewer photos.")
        return self.rfile.read(n)

    # ---- routes ----
    def do_GET(self):
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            if url.path in ("", "/"):
                self.path = "/index.html"
            return super().do_GET()
        if url.path == "/api/version":            # no sign-in: Codi checks any park's version
            return self.reply({"ok": True, **updater.current_version()})
        _touch_activity()
        user = self.user()
        if not user:
            return self.reply({"ok": False, "error": "signin"}, 401)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/api/me":
                cfg = V.load_config()
                return self.reply({"ok": True, "name": user["name"], "mode": cfg["mode"],
                                   "items": cfg["items"], "other": cfg["other"],
                                   "levels": cfg["warning_levels"],
                                   "version": updater.current_version()})
            if url.path == "/api/home":
                parks = [p["park"] for p in V.lots(rmconn.Connection("practice"))]
                active = V.active_violations(rmconn.Connection("practice"), parks)
                return self.reply({"ok": True, "active": active, "parks": parks})
            if url.path == "/api/parks":
                parks = V.lots(rmconn.Connection("practice"))
                return self.reply({"ok": True, "parks": parks})
            if url.path.startswith("/api/tenants/") and url.path.endswith("/history"):
                # fetched by the phone in the background as soon as the lot is picked
                hist = V.tenant_violations(rmconn.Connection("practice"), int(url.path.split("/")[3]))
                return self.reply({"ok": True, "history": hist, "suggest": V.suggest_warning(hist)})
            if url.path.startswith("/api/received/"):
                st = inbox.status(url.path.rsplit("/", 1)[-1], user)
                return self.reply({"ok": bool(st), "status": st} if st else {"ok": False, "error": "not found"},
                                  200 if st else 404)
            if url.path == "/api/recent":
                items = V._read(V.LOG, [])[-25:][::-1]
                return self.reply({"ok": True, "items": [
                    {k: i.get(k) for k in ("ref", "park", "lot", "tenant_name", "warning",
                                           "correct_by", "issued", "test", "history_id",
                                           "photo_count", "printed")} for i in items]})
            if url.path.startswith("/api/letters/"):
                ref = os.path.basename(url.path)[:-4]
                path = os.path.join(V.DATA, "letters", f"{ref}.pdf")
                if not ref.startswith("V-") or not os.path.exists(path):
                    return self.reply({"ok": False}, 404)
                data = open(path, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.reply({"ok": False, "error": "not found"}, 404)
        except Exception as e:
            log("GET " + url.path + " failed\n" + traceback.format_exc())
            self.reply({"ok": False, "error": str(e)}, 500)

    def do_POST(self):
        url = urlparse(self.path)
        ip = self.client_ip()
        _touch_activity()
        try:
            if url.path == "/api/login":
                d = json.loads(self.body() or b"{}")
                name = str(d.get("username", "")).strip().lower()[:64]
                if _too_many(_fails, ip, IP_LIMIT, IP_WINDOW):
                    return self.reply({"ok": False, "error": "Too many tries — wait 15 minutes."}, 429)
                if _too_many(_user_fails, name, USER_LIMIT, USER_WINDOW):
                    return self.reply({"ok": False, "error": "Sign-in for this username is paused for an hour."}, 429)
                u = next((x for x in V.load_config()["users"] if x["username"].lower() == name), None)
                if not u or not u.get("password_hash") or not V.check_password(str(d.get("password", ""))[:200], u["password_hash"]):
                    _fails.setdefault(ip, []).append(time.time())
                    _user_fails.setdefault(name, []).append(time.time())
                    if _too_many(_user_fails, name, USER_LIMIT, USER_WINDOW):
                        _alert(name, ip)
                    time.sleep(1)
                    log(f"failed sign-in for '{name}' from {ip}")
                    return self.reply({"ok": False, "error": "That username or password isn't right."}, 401)
                secure = "; Secure" if self.https() else ""
                cookie = (f"vsess={make_session(u['username'])}; Path=/; HttpOnly; SameSite=Strict; "
                          f"Max-Age={SESSION_DAYS * 86400}{secure}")
                log(f"{u['username']} signed in from {ip} ({(self.headers.get('User-Agent') or '')[:120]})")
                return self.reply({"ok": True, "name": u["name"]}, headers={"Set-Cookie": cookie})
            if url.path == "/api/logout":
                return self.reply({"ok": True}, headers={"Set-Cookie": "vsess=; Path=/; Max-Age=0"})
            user = self.user()
            if not user:
                return self.reply({"ok": False, "error": "signin"}, 401)
            if url.path == "/api/preview":
                d = json.loads(self.body() or b"{}")
                from datetime import date
                due = V.deadline(date.today(), d.get("items", []), d.get("others", []))
                return self.reply({"ok": True, "correct_by": due})
            # ---- a finished violation, sent once in the background (inbox.py) ----
            if url.path == "/api/issue":
                # Clippy's own test scripts send this header. In live mode a
                # test must never reach a real tenant — 2026-09-23 one did
                # (Morristown lot 20) because the mode had been switched to
                # live between tests and nobody checked.
                if self.headers.get("X-Clippy-Test") and V.load_config()["mode"] == "live":
                    return self.reply({"ok": False, "error": "Refused: this is a test and the app is LIVE."}, 409)
                form = self.read_multipart()
                photo = form["photos"][0]["data"] if form["photos"] else None
                st = inbox.submit_violation(user, form, photo, _park(form.get("property_id")))
                return self.reply({"ok": True, "status": st})
            if url.path.startswith("/api/violations/") and url.path.endswith("/fixed"):
                hid = int(url.path.split("/")[3])
                if (self.headers.get("Content-Type") or "").startswith("multipart/form-data"):
                    form = self.read_multipart()
                    photo = form["photos"][0]["data"] if form["photos"] else None
                else:
                    form, photo = json.loads(self.body() or b"{}"), None
                conn = rmconn.Connection("live")
                if self.headers.get("X-Clippy-Test"):
                    orig = conn.get(f"HistoryNotes/{hid}", {"fields": "EntityType"}) or {}
                    if orig.get("EntityType") != "Prospect":
                        return self.reply({"ok": False, "error": "Refused: test on a real tenant's record."}, 409)
                st = inbox.submit_fix(user, form.get("cid"), hid, photo, conn)
                log(f"{user['username']} marked {hid} fixed -> {st.get('result', {}).get('history_id')}"
                    f" ({'photo' if photo else 'no photo'})")
                return self.reply({"ok": True, "status": st})
            self.reply({"ok": False, "error": "not found"}, 404)
        except ValueError as e:
            self.reply({"ok": False, "error": str(e)}, 400)
        except Exception as e:
            log("POST " + url.path + " failed\n" + traceback.format_exc())
            self.reply({"ok": False, "error": str(e)}, 500)

    def read_multipart(self):
        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("multipart/form-data"):
            raise ValueError("Expected a form with photos.")
        raw = self.body()
        msg = BytesParser(policy=email_default).parsebytes(
            b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw)
        form, photos = {}, []
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            data = part.get_payload(decode=True)
            if name == "form":
                form = json.loads(data.decode("utf-8"))
            elif name and name.startswith("photo"):
                photos.append({"name": part.get_filename() or name, "data": data})
        form["photos"] = photos[:20]
        return form


def _park(property_id=None):
    """One of the parks this computer is switched on for (config "parks").
    A manager with two parks (e.g. Freeburg + Mascoutah) picks it on the
    lot screen, which sends its property_id."""
    parks = V.lots(rmconn.Connection("practice"))
    if not parks:
        raise ValueError("No parks are switched on — open Violations Settings on the manager computer.")
    if property_id is None:
        if len(parks) > 1:
            raise ValueError("Pick the park first.")
        return parks[0]
    park = next((p for p in parks if p["property_id"] == int(property_id)), None)
    if not park:
        raise ValueError("That park isn't switched on for this computer.")
    return park


def reminder_loop():
    """Every 30 minutes: any violation deadlines arrived? (Reads Rent Manager.)"""
    time.sleep(20)
    while True:
        try:
            r = V.remind(rmconn.Connection("practice"))
            if r.get("reminders"):
                log(f"sent {len(r['reminders'])} reminder(s)")
        except Exception:
            log("reminder check failed\n" + traceback.format_exc())
        time.sleep(1800)


def main():
    cfg = V.load_config()
    port = cfg["server"]["port"]
    # Only this PC can connect directly. Phones come in through Tailscale
    # Funnel (https://<pc>.<tailnet>.ts.net), which hands requests to this
    # port on 127.0.0.1 — so nothing else on any network can reach it.
    # --lan opens it to the home network as well (no HTTPS there).
    host = "0.0.0.0" if "--lan" in sys.argv else "127.0.0.1"
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    inbox.log = log
    inbox.recover()
    if "--no-reminders" not in sys.argv:
        threading.Thread(target=reminder_loop, daemon=True).start()
    log(f"listening on {host}:{port}")
    print(f"Manager server on http://{host}:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
