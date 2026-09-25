"""Violations Settings — the manager computer's own settings page.

Opens in the browser at http://127.0.0.1:8791 (desktop shortcut made by
install.ps1). Listens on this computer only and is NOT behind Tailscale
Funnel (Funnel forwards port 8790, the phone server) — so nobody on the
internet can reach it. Requests from any other page are refused (Origin check).

What it sets (violations_config.json): the manager's name and phone password,
which parks this computer handles, the printer, the deadlines, and test/live.
"""

import base64
import functools
import http.server
import io
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import traceback
import webbrowser
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import rmconn  # noqa: E402
import updater  # noqa: E402
import violations as V  # noqa: E402

PORT = 8791
UI = os.path.join(HERE, "ui", "settings")
TAILSCALE = r"C:\Program Files\Tailscale\tailscale.exe"
NO_WINDOW = 0x08000000


def _port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _funnel():
    """(installed, signed_in, phone_url)"""
    if not os.path.exists(TAILSCALE):
        return False, False, None
    try:
        st = json.loads(subprocess.run([TAILSCALE, "status", "--json"], capture_output=True, text=True,
                                       timeout=15, creationflags=NO_WINDOW).stdout or "{}")
        fs = json.loads(subprocess.run([TAILSCALE, "funnel", "status", "--json"], capture_output=True,
                                       text=True, timeout=15, creationflags=NO_WINDOW).stdout or "{}")
    except Exception:
        return True, False, None
    host = ((st.get("Self") or {}).get("DNSName") or "").rstrip(".")
    running = st.get("BackendState") == "Running"
    on = any(v for v in (fs.get("AllowFunnel") or {}).values())
    return True, running, (f"https://{host}/" if host and on else None)


def _qr(text):
    try:
        import qrcode
        buf = io.BytesIO()
        qrcode.make(text, box_size=6, border=2).save(buf, "PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


class Api:
    def overview(self):
        cfg = V.load_config()
        today = date.today()
        installed, signed_in, url = _funnel()
        return {
            "ok": True, "mode": cfg["mode"], "parks": cfg.get("parks") or [],
            "items": [dict(i, example=str(V.item_deadline(today, i["days"], i["rule"]))) for i in cfg["items"]],
            "other": dict(cfg["other"], example=str(V.item_deadline(today, cfg["other"]["days"], cfg["other"]["rule"]))),
            "printer": cfg.get("printer"), "print_photos": cfg.get("print_photos"),
            "user": {k: cfg["users"][0].get(k) for k in ("username", "name")} |
                    {"has_password": bool(cfg["users"][0].get("password_hash"))},
            "server_up": _port_open(cfg["server"]["port"]), "port": cfg["server"]["port"],
            "tailscale": {"installed": installed, "signed_in": signed_in, "url": url, "qr": _qr(url) if url else None},
            "rm_set_up": os.path.exists(rmconn.CONFIG),
            "updates": self.update_status(),
            "recent": [{k: r.get(k) for k in ("park", "lot", "tenant_name", "warning", "issued", "correct_by", "test")}
                       for r in V._read(V.LOG, [])[-10:][::-1]]}

    def check_rm(self):
        try:
            c = rmconn.Connection("practice")
            props = c.get("Properties", {"fields": "PropertyID,ShortName"}) or []
            return {"ok": True, "parks": sorted(p["ShortName"] for p in props), "rate": c.rate()["text"]}
        except Exception as e:
            return {"ok": False, "error": rmconn._msg(e) if hasattr(e, "body") else str(e)}

    def save(self, items=None, other=None, printer="__keep__", print_photos=None, parks=None,
             name=None, username=None):
        cfg = V.load_config()
        if items:
            by_id = {i["id"]: i for i in items}
            for it in cfg["items"]:
                n = by_id.get(it["id"])
                if n:
                    it["days"] = max(0, min(90, int(n["days"])))
                    it["rule"] = "next_monday" if n["rule"] == "next_monday" else "exact"
        if other:
            cfg["other"] = {"days": max(0, min(90, int(other["days"]))),
                            "rule": "next_monday" if other["rule"] == "next_monday" else "exact"}
        if printer != "__keep__":
            cfg["printer"] = printer or None
        if print_photos is not None:
            cfg["print_photos"] = bool(print_photos)
        if parks is not None:
            cfg["parks"] = [str(p) for p in parks][:10]
            V._lots_cache["data"] = None
        if name is not None and name.strip():
            cfg["users"][0]["name"] = name.strip()[:60]
        if username is not None and username.strip():
            cfg["users"][0]["username"] = username.strip().lower()[:32]
        V.save_config(cfg)
        return self.overview()

    def set_mode(self, mode):
        cfg = V.load_config()
        cfg["mode"] = "live" if mode == "live" else "test"
        V.save_config(cfg)
        return self.overview()

    def set_password(self, password):
        # The phone sign-in page is reachable from the internet (Funnel):
        # the password is the only lock.
        if len(password or "") < 12:
            return {"ok": False, "error": "Use at least 12 characters — a short sentence works well."}
        cfg = V.load_config()
        if password.lower() == cfg["users"][0]["username"].lower():
            return {"ok": False, "error": "Pick something harder to guess."}
        cfg["users"][0]["password_hash"] = V.hash_password(password)
        V.save_config(cfg)
        return {"ok": True}

    def printers(self):
        try:
            return {"ok": True, "printers": V.printers()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def test_print(self, printer=None):
        cfg = V.load_config()
        printer = printer or cfg.get("printer")
        if not printer:
            return {"ok": False, "error": "Pick a printer first."}
        it = cfg["items"][4]
        v = {"ref": "V-TESTPRINT", "issued": date.today(), "park": "TEST PARK", "park_phone": "(000) 000-0000",
             "lot": "0", "tenant_name": "TEST PRINT - not a real notice", "item_ids": [it["id"]],
             "item_labels": [it["label"]], "others": ["This is a test page"],
             "notes": "Printed from Violations Settings to check the printer.", "warning": "Reminder",
             "correct_by": date.today(), "issued_by": cfg["users"][0]["name"], "photos": []}
        os.makedirs(os.path.join(V.DATA, "letters"), exist_ok=True)
        return V.print_pdf(V.render_pdf(v, cfg, with_photos=False), printer, "V-TESTPRINT")

    def update_status(self):
        st, v = updater.status(), updater.current_version()
        return {"dev_copy": v["dev"], "version": v["version"], "installed_at": v["installed_at"],
                "last_check": st.get("last_check"), "result": st.get("result"),
                "problem": bool(st.get("problem")),
                "checking": os.path.exists(os.path.join(updater.WORK, "running"))}

    def check_updates(self):
        """Runs updater.py --now on its own (it may restart the phone server)."""
        subprocess.Popen([os.path.join(HERE, "venv", "Scripts", "pythonw.exe"),
                          os.path.join(HERE, "updater.py"), "--now"], cwd=HERE, creationflags=NO_WINDOW)
        return {"ok": True}

    def restart_server(self):
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            os.path.join(HERE, "install.ps1"), "-RestartOnly"],
                           capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW)
        return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr)[-500:]}


EXPOSED = {"overview", "check_rm", "save", "set_mode", "set_password", "printers", "test_print",
           "restart_server", "update_status", "check_updates"}


class Handler(http.server.SimpleHTTPRequestHandler):
    api = Api()

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def do_POST(self):
        name = self.path.split("?")[0].removeprefix("/api/")
        origin = self.headers.get("Origin")
        if not self.path.startswith("/api/") or name not in EXPOSED or \
                (origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")):
            self.send_error(403)
            return
        try:
            kwargs = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            result = getattr(self.api, name)(**kwargs)
        except Exception as e:
            traceback.print_exc()
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        body = json.dumps(result, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    url = f"http://127.0.0.1:{PORT}/index.html"
    if _port_open(PORT):                       # already open — just show it
        webbrowser.open(url)
        return
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), functools.partial(Handler, directory=UI))
    httpd.daemon_threads = True
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    httpd.serve_forever()


if __name__ == "__main__":
    main()
