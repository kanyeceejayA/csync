"""A small CSWeb sync-API client - standard library only, no pip install.

The API is the one CSEntry itself uses ("CSPro Sync" v2.0): everything lives
under `{server}/api`, and every call except `/token` needs a bearer token.
The full spec ships with this repo - see `docs/csweb-swagger.json`, or open
`docs/api.html` (also served at /api-docs when the web UI is running).

Facts baked in here, learned from real CSWeb servers:
  * `POST /dictionaries/` needs its trailing slash (Symfony route).  Without
    it the server returns a 404 HTML page and looks like it has no upload.
  * Cases page through *headers*, not query parameters.
  * The case POST body is a bare JSON array, best sent in batches (~200).
  * An HTML body instead of the JSON {"code","description"} envelope means
    Symfony errored - usually a wrong path shape, not a wrong password.
  * `DELETE /dictionaries/{d}/cases/{id}` is unreliable on some builds; the
    safe way to remove a case is a tombstone (`deleted: true`), which is also
    what tablets expect.  See `csync.sync.remove_case`.
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

#: The OAuth client every CSWeb install ships with - not a per-user secret.
CLIENT_ID, CLIENT_SECRET = "cspro_android", "cspro"


class CSWebError(RuntimeError):
    """Any non-2xx answer from the server, with the useful bits extracted."""

    def __init__(self, message, status=None, body=""):
        super().__init__(message)
        self.status = status
        self.body = body


class CSWeb:
    """Talks to one CSWeb server.

        api = CSWeb(url="https://server/csweb/api", user="admin", password="...")
        api.dictionaries()
    """

    def __init__(self, url, user, password, device="csync", timeout=300, verify_tls=True):
        base = (url or "").strip().rstrip("/")
        if base and not base.lower().endswith("/api"):
            base += "/api"                       # "/api" is optional in config
        self.base = base
        self.user = user
        self.password = password
        self.device = device
        self.timeout = timeout
        self._ssl = None if verify_tls else ssl._create_unverified_context()
        self._token = None
        self._token_expires = 0.0

    # ---- plumbing -----------------------------------------------------------
    def token(self) -> str:
        """A bearer token, cached until shortly before it expires."""
        if self._token and time.time() < self._token_expires:
            return self._token
        body = json.dumps({
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "grant_type": "password", "username": self.user, "password": self.password,
        }).encode()
        status, text, _ = self._raw("POST", "/token", body,
                                    {"Content-Type": "application/json"}, auth=False)
        try:
            data = json.loads(text)
        except ValueError:
            data = {}
        if status >= 400 or not data.get("access_token"):
            hint = ("the server returned an HTML error page" if "<html" in text.lower()
                    else data.get("error_description") or data.get("message") or text[:200])
            raise CSWebError(f"CSWeb sign-in failed (HTTP {status}): {hint}", status, text)
        self._token = data["access_token"]
        self._token_expires = time.time() + max(60, int(data.get("expires_in", 3600)) - 120)
        return self._token

    def _raw(self, method, path, body=None, headers=None, auth=True):
        """One HTTP round trip.  Returns (status, text, response headers)."""
        headers = dict(headers or {})
        if auth:
            headers["Authorization"] = "Bearer " + self.token()
        headers.setdefault("Accept", "application/json")
        req = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl) as r:
                return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
        except urllib.error.HTTPError as e:                  # 4xx/5xx still have a body
            return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)
        except urllib.error.URLError as e:
            raise CSWebError(f"cannot reach {self.base}: {e.reason}") from None

    def request(self, method, path, *, body=None, headers=None, text=False):
        """A checked call.  Returns (payload, response headers)."""
        status, raw, resp_headers = self._raw(method, path, body, headers)
        if status >= 400:
            if "<html" in raw[:400].lower():
                hint = "the server returned an HTML error page (check the path shape)"
            else:
                try:
                    hint = json.loads(raw).get("description") or raw[:300]
                except ValueError:
                    hint = raw[:300]
            raise CSWebError(f"{method} {path} -> HTTP {status}: {hint}", status, raw)
        if text:
            return raw, resp_headers
        try:
            return json.loads(raw) if raw.strip() else None, resp_headers
        except ValueError:
            return raw, resp_headers

    # ---- server & dictionaries ---------------------------------------------
    def server(self) -> dict:
        """`{"deviceId": ..., "apiVersion": ...}` - the cheapest health check."""
        return self.request("GET", "/server")[0]

    def dictionaries(self) -> list[dict]:
        """Every dictionary on the server with its label and case count."""
        return self.request("GET", "/dictionaries")[0] or []

    def dictionary_text(self, name: str) -> str:
        """The .dcf text of one dictionary."""
        return self.request("GET", f"/dictionaries/{_q(name)}", text=True)[0]

    def add_dictionary(self, dcf_text: str) -> dict:
        """Register (or replace) a dictionary.  The trailing slash is required."""
        return self.request("POST", "/dictionaries/", body=dcf_text.encode("utf-8"),
                            headers={"Content-Type": "text/plain"})[0]

    def delete_dictionary(self, name: str) -> dict:
        """Delete a dictionary AND every case in it.  There is no undo."""
        return self.request("DELETE", f"/dictionaries/{_q(name)}")[0]

    # ---- cases --------------------------------------------------------------
    def cases(self, name, *, universe=None, since_etag=None, page=100000, limit=None):
        """Download cases (active and tombstoned).

        Returns `(cases, etag)`.  Keep the etag and pass it back as
        `since_etag` next time to get only what changed - the server answers
        412 if it no longer knows that etag, and this raises so the caller can
        fall back to a full pull.
        """
        out, etag, after = [], None, None
        while True:
            headers = {"x-csw-case-range-count": str(page), "x-csw-device": self.device}
            if universe:
                headers["x-csw-universe"] = universe
            if after:
                headers["x-csw-case-range-start-after"] = after
            if since_etag and not out:
                headers["If-Match"] = since_etag
            payload, resp = self.request("GET", f"/dictionaries/{_q(name)}/cases",
                                         headers=headers)
            batch = payload if isinstance(payload, list) else []
            out.extend(batch)
            etag = resp.get("etag") or resp.get("ETag") or etag
            if not batch or len(batch) < page or (limit and len(out) >= limit):
                break
            last = batch[-1].get("id")
            if last == after:                 # some builds loop instead of advancing
                break
            after = last
        return (out[:limit] if limit else out), etag

    def case(self, name: str, case_id: str) -> dict:
        """One case by GUID."""
        return self.request("GET", f"/dictionaries/{_q(name)}/cases/{_q(case_id)}")[0]

    def post_cases(self, name, cases, *, batch=200, on_progress=None):
        """Add or update cases.  The body is a bare JSON array, sent in batches."""
        for start in range(0, len(cases), batch):
            chunk = cases[start:start + batch]
            self.request("POST", f"/dictionaries/{_q(name)}/cases",
                         body=json.dumps(chunk).encode(),
                         headers={"Content-Type": "application/json",
                                  "x-csw-device": self.device})
            if on_progress:
                on_progress(min(start + batch, len(cases)), len(cases))
        return len(cases)

    def delete_case(self, name: str, case_id: str):
        """Hard delete.  Broken on some production builds - prefer a tombstone."""
        return self.request("DELETE", f"/dictionaries/{_q(name)}/cases/{_q(case_id)}")[0]

    def syncs(self, name: str, limit=50) -> list[dict]:
        """Sync history: which device synced this dictionary, which way, when."""
        return self.request("GET", f"/dictionaries/{_q(name)}/syncs?limit={int(limit)}")[0] or []

    # ---- files & apps -------------------------------------------------------
    def apps(self) -> list[dict]:
        """Deployed application packages."""
        return self.request("GET", "/apps")[0] or []

    def folder(self, path: str) -> dict:
        """List a server folder.  No leading slash: `apps`, not `/apps`."""
        return self.request("GET", f"/folders/{urllib.parse.quote(path.lstrip('/'))}")[0]

    def file_info(self, path: str) -> dict:
        return self.request("GET", f"/files/{urllib.parse.quote(path.lstrip('/'))}")[0]


def _q(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


# ---- vector clocks ----------------------------------------------------------
def bump_clock(server_clock, device, boost=False):
    """A clock that dominates the server's, so devices accept our version.

    CSPro only takes a case whose clock dominates the one it holds - the
    server's revision must be >= the device's for *every* entry.  A normal
    bump (+1 on our own entry) is enough for a case only we touch.

    `boost` is the repair recipe for a case a tablet also edited: +500 on
    every existing entry and ours at >= 1000, so the tablet cannot win.  It
    discards whatever that tablet held, so use it deliberately.
    """
    clock = [{"deviceId": e.get("deviceId"), "revision": int(e.get("revision") or 0)}
             for e in (server_clock or [])]
    if boost:
        for e in clock:
            e["revision"] += 500
    mine = next((e for e in clock if e["deviceId"] == device), None)
    if mine:
        mine["revision"] = max(mine["revision"], 1000) if boost else mine["revision"] + 1
    else:
        clock.append({"deviceId": device, "revision": 1000 if boost else 1})
    return clock
