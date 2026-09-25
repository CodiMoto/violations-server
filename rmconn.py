"""One connection to Rent Manager for the violations server.

Reads the login from config.json next to this file (never committed — see
config.example.json). Each manager's computer uses its OWN Rent Manager API
user (Codi's decision, 2026-09-24), so one computer can be switched off
without touching the others, and notes show who wrote them.

  practice  reads Rent Manager; any write is only recorded, never sent
  live      reads and writes

One Rent Manager user may hold only TEN API tokens at once, so the token is
cached in data/.token and re-used (rmclient.py handles that).
"""

import copy
import json
import os
import time

import rmclient

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")


def load_rm_config():
    if not os.path.exists(CONFIG):
        raise RuntimeError("Rent Manager isn't set up on this computer yet — run install.ps1.")
    with open(CONFIG, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    cfg.setdefault("company", "twinpm")
    cfg.setdefault("base_url", f"https://{cfg['company']}.api.rentmanager.com/")
    cfg.setdefault("location_id", 1)
    cfg["token_cache"] = os.path.join(HERE, "data", ".token")
    return cfg


class Connection:
    def __init__(self, target="practice", verbose=False):
        self.target = target
        self.dry_run = target != "live"
        self.steps = []
        os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
        self.rm = rmclient.RentManager(config=load_rm_config(), verbose=verbose)
        self.server = self.rm.base
        self._fake_id = 0

    def get(self, path, params=None, note=None):
        t0 = time.time()
        try:
            body, resp = self.rm.request("GET", path, params=params)
        except rmclient.RentManagerError as e:
            self._step("read", "GET", path, note, e.status, t0, error=_msg(e))
            raise
        self._step("read", "GET", path, note, resp.status_code, t0)
        return body

    def write(self, method, path, params=None, json_body=None, files=None,
              data=None, note=None, fake_response=None):
        if self.dry_run:
            self._fake_id -= 1
            self._step("write", method, path, note, None, time.time(), sent=False)
            return fake_response(self._fake_id) if fake_response else None
        t0 = time.time()
        try:
            body, resp = self.rm.request(method, path, params=params, json_body=json_body,
                                         files=files, data=data)
        except rmclient.RentManagerError as e:
            self._step("write", method, path, note, e.status, t0, error=_msg(e), sent=False)
            raise
        self._step("write", method, path, note, resp.status_code, t0, sent=True)
        return body

    def _step(self, kind, method, path, note, status, t0, error=None, sent=None):
        self.steps.append({"kind": kind, "method": method, "path": path, "note": note,
                           "status": status, "ms": int((time.time() - t0) * 1000),
                           "error": error, "sent": sent})

    def rate(self):
        return {"remaining": self.rm.rate_remaining, "limit": self.rm.rate_limit,
                "text": self.rm.rate_status()}


def _msg(e):
    """Rent Manager puts the useful sentence in DeveloperMessage/UserMessage."""
    try:
        b = json.loads(e.body)
        return b.get("UserMessage") if b.get("UserMessage") not in (
            None, "", "Unspecified Error") else b.get("DeveloperMessage") or b.get("Message") or e.body
    except (ValueError, TypeError, AttributeError):
        return str(e.body)[:500]
