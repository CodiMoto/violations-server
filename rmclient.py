"""Rent Manager 12 Web API client.

Handles the things that make this API awkward: token auth that expires without
warning, a hard cap on how many tokens one user may hold, a rate limit we must
not blow, and pagination that kicks in silently above 1000 records.

Two hosts, and the difference matters:

  classic  https://twinpm.api.rentmanager.com/     <- what we use now
  Express  https://twinpm.rmx.rentmanager.com/api/ <- the old fallback

Both speak the same resource model, so everything above the transport is
identical. Only authentication differs, and the classic host is the licensed
one as of Sept 2026 — it is also the only one that lets us POST images.

Docs: the API's own portal at https://twinpm.api.rentmanager.com (needs a
browser login), plus "API Getting Started (twinpm).pdf" in this folder.
"""

import json
import os
import time
from urllib.parse import urljoin

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
TOKEN_CACHE = os.path.join(HERE, "data", ".token")

TOKEN_HEADER = "X-RM12Api-ApiToken"
LOCATION_HEADER = "X-RM12Api-LocationID"
PARTNER_HEADER = "X-RM12Api-PartnerToken"

# A classic-API token lives 24 hours, but dies after 15 minutes of inactivity.
# We re-use a cached one well inside the 24h window and let a 401 tell us when
# idleness has killed it — that costs one wasted request, never a wrong answer.
TOKEN_MAX_AGE_S = 23 * 3600

# One user may hold ten valid tokens at once. Authenticating an eleventh time
# fails outright, so every caller MUST re-use the cache rather than logging in
# per run. We keep the tokens we were issued so we can free a slot ourselves if
# we ever do hit the cap.
MAX_TOKENS = 10
MAX_LOGINS_MARKER = "maximum number of times"


class RateLimitLow(Exception):
    """Raised when we're close enough to the hourly cap that we should stop."""


class RentManagerError(Exception):
    def __init__(self, status, body, url):
        self.status, self.body, self.url = status, body, url
        super().__init__(f"HTTP {status} on {url}\n{body}")


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    # Express and classic WAPI12 both take a username/password; only the
    # endpoint differs (see RentManager.authenticate).
    required = ("username", "password")
    missing = [k for k in ("company", "base_url") + required
               if not cfg.get(k) or str(cfg.get(k, "")).startswith("PASTE_")]
    if missing:
        raise SystemExit(
            f"config.json still needs: {', '.join(missing)}\n"
            f"Fill them in at {CONFIG}")
    return cfg


class RentManager:
    def __init__(self, config=None, verbose=True):
        self.cfg = config or load_config()
        # Rent Manager Express serves the API from the customer's own RMX host.
        # Classic WAPI12 uses {company}.api.rentmanager.com, which Express
        # accounts are not licensed for — so base_url wins when it's set.
        self.base = self.cfg.get("base_url") or \
            f"https://{self.cfg['company']}.api.rentmanager.com/"
        if not self.base.endswith("/"):
            self.base += "/"
        self.express = "rmx." in self.base
        self.verbose = verbose
        # A second server (e.g. a Rent Manager sandbox, if one is ever issued)
        # must keep its tokens in its own file: sharing .token would overwrite
        # the live host's record of issued tokens, the only list we can use to
        # free a slot under the ten-token cap.
        self.token_cache = self.cfg.get("token_cache") or TOKEN_CACHE
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        # Every request is scoped to one location; without this header the API
        # silently uses the user's default. twinpm has only one, but being
        # explicit means a second location can never quietly change our answers.
        if not self.express and self.cfg.get("location_id"):
            self.session.headers[LOCATION_HEADER] = str(self.cfg["location_id"])
        self.token = None
        # Populated from response headers on every call.
        self.rate_limit = None
        self.rate_remaining = None
        self.rate_reset_s = None
        self.requests_made = 0
        self._load_cached_token()

    # ---- auth ---------------------------------------------------------
    def _read_cache(self):
        try:
            with open(self.token_cache, encoding="utf-8") as f:
                cached = json.load(f)
        except (json.JSONDecodeError, OSError, FileNotFoundError):
            return {}
        # A token is only valid for the host and company it was issued against.
        if cached.get("company") != self.cfg["company"] or \
                cached.get("base") not in (None, self.base):
            return {}
        return cached

    def _write_cache(self, cached):
        try:
            with open(self.token_cache, "w", encoding="utf-8") as f:
                json.dump(cached, f)
        except OSError:
            pass    # a cache we cannot write is slow, not wrong

    def _load_cached_token(self):
        cached = self._read_cache()
        token = cached.get("token")
        if not token:
            return
        if time.time() - (cached.get("obtained") or 0) > TOKEN_MAX_AGE_S:
            return      # past its 24h life; don't waste a request proving it
        self.token = token
        self.session.headers[TOKEN_HEADER] = token

    def authenticate(self):
        """Trade long-lived credentials for a short-lived API token.

        Express (RMX) only accepts a Partner ID + Partner Token issued by Rent
        Manager, passed in the X-RM12Api-PartnerToken header — it rejects
        AuthorizeUser outright with "not supported by the RMX Web API".
        Classic WAPI12 hosts still use the username/password flow.
        """
        headers = {}
        if self.express:
            # Express: username/password to ExpressAuthentication/Authenticate,
            # which sets an ASP.NET_SessionId cookie the session then carries.
            # (AuthorizeUser is explicitly unsupported on RMX; AuthorizePartner
            # exists but needs partner credentials issued by Rent Manager.)
            url = urljoin(self.base, "ExpressAuthentication/Authenticate")
            payload = {
                "Username": self.cfg["username"],
                "Password": self.cfg["password"],
                "CompanyCode": self.cfg["company"],
            }
        elif self.cfg.get("partner_token"):
            url = urljoin(self.base, "Authentication/AuthorizePartner")
            headers[PARTNER_HEADER] = self.cfg["partner_token"]
            payload = {"PartnerID": self.cfg.get("partner_id")}
        else:
            url = urljoin(self.base, "Authentication/AuthorizeUser")
            payload = {
                "Username": self.cfg["username"],
                "Password": self.cfg["password"],
            }
        if self.cfg.get("location_id"):
            payload["LocationID"] = self.cfg["location_id"]

        resp = self.session.post(url, json=payload, headers=headers, timeout=60)
        self._read_rate_headers(resp)

        if resp.status_code >= 300:
            if MAX_LOGINS_MARKER in resp.text and self._free_a_token_slot():
                resp = self.session.post(url, json=payload, headers=headers,
                                         timeout=60)
                self._read_rate_headers(resp)
            if resp.status_code >= 300:
                raise RentManagerError(resp.status_code, resp.text[:800], url)

        if self.express:
            # Nothing to store: auth lives in the session cookie jar.
            if "ASP.NET_SessionId" not in self.session.cookies:
                raise RentManagerError(resp.status_code,
                                       "no session cookie returned", url)
            self.token = "cookie"
            if self.verbose:
                print(f"  authenticated as {self.cfg['username']} (session)")
            return self.token

        # The API returns the token as a bare quoted string.
        token = resp.text.strip().strip('"')
        if not token or len(token) < 8:
            raise RentManagerError(resp.status_code,
                                   f"unexpected token payload: {resp.text[:200]}",
                                   url)

        self.token = token
        self.session.headers[TOKEN_HEADER] = token
        # Remember every token we were issued, oldest first. If we ever hit the
        # ten-token cap this list is the only way to give a slot back — a token
        # we did not record is one we can never deauthorise.
        issued = [t for t in self._read_cache().get("issued", []) if t != token]
        issued.append(token)
        self._write_cache({"company": self.cfg["company"], "base": self.base,
                           "token": token, "obtained": time.time(),
                           "issued": issued[-MAX_TOKENS:]})
        if self.verbose:
            print(f"  authenticated as {self.cfg['username']}")
        return token

    def deauthorize(self, token):
        """Hand one token's slot back. Best effort; never raises."""
        try:
            self.session.post(urljoin(self.base, "Authentication/Deauthorize"),
                              params={"token": token}, timeout=30)
        except requests.RequestException:
            return False
        return True

    def _free_a_token_slot(self):
        """All ten slots are taken. Retire the oldest token we still know of.

        Slots also free themselves after 15 minutes of inactivity, so this only
        matters when something has been authenticating in a tight loop instead
        of re-using the cache. Returns True if a slot was actually released.
        """
        cached = self._read_cache()
        issued = cached.get("issued") or []
        # Never retire the token we are still working with — freeing a slot by
        # killing our own credential just moves the failure one request later.
        candidates = [t for t in issued if t != self.token]
        if not candidates:
            return False
        oldest = candidates[0]
        if self.verbose:
            print("  ten tokens already issued; retiring the oldest")
        self.deauthorize(oldest)
        cached["issued"] = [t for t in issued if t != oldest]
        self._write_cache(cached)
        return True

    def ensure_token(self):
        if not self.token:
            self.authenticate()

    # ---- rate limiting -------------------------------------------------
    def _read_rate_headers(self, resp):
        """The two hosts report the limit differently.

        classic: x-ratelimit-limit / -remaining / -reset (an epoch timestamp),
                 500 requests per ROLLING MINUTE.
        Express: X-RateLimit / X-RateRemaining / X-RateTimeLeft (seconds left),
                 a much smaller hourly budget.
        """
        def as_int(name):
            try:
                return int(resp.headers[name])
            except (KeyError, ValueError, TypeError):
                return None

        self.rate_limit = as_int("x-ratelimit-limit") or \
            as_int("X-RateLimit") or self.rate_limit
        remaining = as_int("x-ratelimit-remaining")
        if remaining is None:
            remaining = as_int("X-RateRemaining")
        if remaining is not None:
            self.rate_remaining = remaining

        reset_epoch = as_int("x-ratelimit-reset")
        if reset_epoch is not None:
            self.rate_reset_s = max(0, reset_epoch - int(time.time()))
        else:
            self.rate_reset_s = as_int("X-RateTimeLeft") or self.rate_reset_s

    def rate_status(self):
        if self.rate_limit is None:
            return "rate limit unknown (no headers seen yet)"
        secs = self.rate_reset_s or 0
        window = f"{secs}s" if secs < 120 else f"{secs / 60:.0f} min"
        return (f"{self.rate_remaining}/{self.rate_limit} requests left, "
                f"resets in {window}")

    def _check_rate_floor(self):
        """Stay off the limit.

        On the classic host the window is a rolling minute, so running out is a
        pause, not a failure — waiting for the reset is always cheaper than
        abandoning a half-built cache. Express's budget is hourly, so there we
        still stop and let the caller decide.
        """
        floor = self.cfg.get("rate_floor", 25)
        if self.rate_remaining is None or self.rate_remaining > floor:
            return
        if self.express:
            raise RateLimitLow(
                f"only {self.rate_remaining} requests left this hour "
                f"(floor is {floor}); resets in "
                f"{(self.rate_reset_s or 0)/60:.0f} min")
        wait = min((self.rate_reset_s or 0) + 1, 65)
        if self.verbose:
            print(f"  rate limit low ({self.rate_remaining} left); "
                  f"waiting {wait}s for the window to reset")
        time.sleep(wait)
        self.rate_remaining = None      # unknown again until the next response

    # ---- core request --------------------------------------------------
    def request(self, method, path, params=None, json_body=None, retry_auth=True,
                files=None, data=None):
        """`files`/`data` send multipart/form-data instead of JSON — the only
        way the API accepts a new file (see Rent Manager's "Attachments Master"
        Postman collection, ticket 2708217): the records go as JSON in a form
        field called `body`, and each file is a form part whose name matches a
        `"MetaTag"` inside that JSON. Pass file contents as bytes, not open
        handles, so the one 401 replay below can resend them."""
        self.ensure_token()
        self._check_rate_floor()

        url = urljoin(self.base, path.lstrip("/"))
        extra = {}
        if files is not None or data is not None:
            # The session defaults to JSON; None drops that header so requests
            # can write multipart/form-data with its boundary.
            extra = {"files": files, "data": data,
                     "headers": {"Content-Type": None}}
        else:
            extra = {"json": json_body}
        resp = self.session.request(method, url, params=params, timeout=120,
                                    **extra)
        self.requests_made += 1
        self._read_rate_headers(resp)

        if resp.status_code == 401 and retry_auth:
            # Fifteen minutes of idleness kills a token with no warning. Get a
            # fresh one and replay once — the cached token is simply stale.
            if self.verbose:
                print("  token rejected, re-authenticating")
            self.token = None
            self.session.headers.pop(TOKEN_HEADER, None)
            self.authenticate()
            return self.request(method, path, params, json_body,
                                retry_auth=False, files=files, data=data)

        if resp.status_code == 429:
            # The classic window is a rolling minute, so this is worth sitting
            # out once rather than losing the work done so far.
            if not self.express and retry_auth:
                wait = min((self.rate_reset_s or 0) + 1, 65)
                if self.verbose:
                    print(f"  rate limited; waiting {wait}s")
                time.sleep(wait)
                return self.request(method, path, params, json_body,
                                    retry_auth=False, files=files, data=data)
            raise RateLimitLow(f"rate limited; {self.rate_status()}")

        if resp.status_code >= 400:
            raise RentManagerError(resp.status_code, resp.text[:1500], resp.url)

        if resp.status_code == 204 or not resp.content:
            return None, resp
        try:
            return resp.json(), resp
        except json.JSONDecodeError:
            return resp.text, resp

    # ---- reads ----------------------------------------------------------
    def get(self, resource, fields=None, embeds=None, filters=None,
            orderby=None, params=None):
        """Single GET. Returns the parsed body."""
        q = dict(params or {})
        if fields:
            q["fields"] = fields if isinstance(fields, str) else ",".join(fields)
        if embeds:
            q["embeds"] = embeds if isinstance(embeds, str) else ",".join(embeds)
        if filters:
            q["filters"] = filters
        if orderby:
            q["orderby"] = orderby
        body, _ = self.request("GET", resource, params=q)
        return body

    def get_all(self, resource, fields=None, embeds=None, filters=None,
                orderby=None, page_size=None, max_pages=None):
        """GET every page of a collection, respecting the rate limit.

        Pagination turns on automatically above 1000 records, so we always
        page explicitly and stop when a short page comes back.
        """
        size = page_size or self.cfg.get("page_size", 1000)
        out, page = [], 1
        while True:
            q = {"pagenumber": page, "pagesize": size}
            if fields:
                q["fields"] = fields if isinstance(fields, str) else ",".join(fields)
            if embeds:
                q["embeds"] = embeds if isinstance(embeds, str) else ",".join(embeds)
            if filters:
                q["filters"] = filters
            if orderby:
                q["orderby"] = orderby

            body, resp = self.request("GET", resource, params=q)
            batch = body if isinstance(body, list) else ([] if body is None else [body])
            out.extend(batch)

            total = resp.headers.get("X-Total-Results")
            if self.verbose:
                print(f"    {resource} page {page}: +{len(batch)} "
                      f"(have {len(out)}{'/' + total if total else ''})")

            if len(batch) < size:
                break
            page += 1
            if max_pages and page > max_pages:
                break
        return out

    # ---- writes ----------------------------------------------------------
    def post(self, resource, payload, save_options=None, nocontent=False):
        """POST to a collection creates; POST to an instance partial-updates."""
        params = {}
        if save_options:
            params["saveOptions"] = save_options
        if nocontent:
            params["nocontent"] = "true"
        body, resp = self.request("POST", resource, params=params,
                                  json_body=payload)
        return body, resp


    # ---- releasing the licence -------------------------------------------
    def logout(self):
        """Finish with the connection.

        On Express this is mandatory. Express licenses a fixed number of
        CONCURRENT logins, and an abandoned session holds one until it times
        out; running short scripts without logging out exhausts them and
        everything then fails with "All available logins are currently in use"
        — which is what happened on 2026-08-21 and took the integration down.

        On the classic host it is deliberately a no-op. A token there is not a
        seat to give back, it is a 24-hour credential we WANT to keep and
        re-use: throwing it away would mean a fresh login on every run, and ten
        of those is the cap. The slot is only really freed by deauthorize().
        """
        if not self.express or not self.token:
            return
        try:
            self.session.post(urljoin(self.base, "ExpressAuthentication/Logout"),
                              timeout=30)
        except requests.RequestException:
            pass
        self.token = None

    def __enter__(self):
        self.ensure_token()
        return self

    def __exit__(self, *exc):
        self.logout()
        return False


def connect(verbose=True):
    """Open a session. Prefer `with connect() as rm:` so the licence is released."""
    rm = RentManager(verbose=verbose)
    rm.ensure_token()
    return rm
