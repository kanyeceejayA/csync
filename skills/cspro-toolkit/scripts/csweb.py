#!/usr/bin/env python3
"""
csweb.py — minimal CLI client for a CSPro / CSWeb sync server REST API.

No third-party deps (stdlib urllib only). Mirrors the CSPro Sync API
(see reference/csweb-swagger.json). Lets you inspect a live CSWeb 8 server:
list/download/upload/delete dictionaries, read cases, view sync history,
browse files/folders, list/download apps, list users, and check server info.

AUTH (OAuth2 password grant, exactly as the CSWeb login + Android client do):
    POST {base}/api/token
      Content-Type: application/json
      {"client_id":"cspro_android","client_secret":"cspro",
       "grant_type":"password","username":U,"password":P}
    -> {"access_token","token_type":"Bearer","expires_in","refresh_token", ...}
Every other endpoint requires:  Authorization: Bearer <access_token>
(that includes /server — only /token is unauthenticated).

USAGE
    python csweb.py --url URL --user U --password P <command> [args]
    python csweb.py --url URL --token TOKEN <command> [args]   # reuse a token
    # creds can also come from env: CSWEB_URL, CSWEB_USER, CSWEB_PASSWORD, CSWEB_TOKEN

COMMANDS
    login                       get an access token and print the JSON
    server                      GET /server               (deviceId, apiVersion)
    dicts                       GET /dictionaries          (name, label, caseCount)
    dict-get NAME [--out F]     GET /dictionaries/NAME     (download .dcf text)
    dict-add FILE               POST /dictionaries/        (upload a .dcf as text;
                                                            the trailing slash is required)
    dict-delete NAME            DELETE /dictionaries/NAME  (deletes data too!)
    cases NAME [opts]           GET /dictionaries/NAME/cases
        --universe U  --count N  --start-after GUID  --device ID  --if-match ETAG
    case-get NAME CASEID [--out F]   GET /dictionaries/NAME/cases/CASEID
    syncs NAME [opts]           GET /dictionaries/NAME/syncs (sync history)
        --from RFC3339  --to RFC3339  --device ID  --limit N  --offset N
    apps                        GET /apps                  (deployable app packages)
    app-get NAME --out F        GET /apps/NAME             (download .csapb binary)
    users                       GET /users
    user-get NAME               GET /users/NAME
    file PATH [--out F]         GET /files/PATH/content    (download a file)
    file-info PATH              GET /files/PATH            (file metadata)
    folder PATH                 GET /folders/PATH          (list a server folder;
                                                            PATH takes NO leading slash:
                                                            "apps/LMS", not "/apps/LMS")

Exit code 0 on HTTP 2xx, 1 otherwise. Add --insecure to skip TLS verification,
--raw to print the response body verbatim (no pretty-print), -v to trace requests.
"""
import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

CLIENT_ID = "cspro_android"
CLIENT_SECRET = "cspro"


def _eprint(*a):
    print(*a, file=sys.stderr)


class CSWeb:
    def __init__(self, base, token=None, insecure=False, verbose=False):
        # Accept ".../api", ".../api/", or bare host; normalise to a "{base}/api" root.
        base = base.rstrip("/")
        if base.endswith("/api"):
            base = base[: -len("/api")]
        self.root = base + "/api"
        self.token = token
        self.verbose = verbose
        self.ctx = ssl._create_unverified_context() if insecure else None

    def _request(self, method, path, *, headers=None, data=None, auth=True):
        url = self.root + path
        hdrs = dict(headers or {})
        if auth and self.token:
            hdrs["Authorization"] = "Bearer " + self.token
        if self.verbose:
            _eprint(f"--> {method} {url}")
            for k, v in hdrs.items():
                shown = v if k != "Authorization" else v[:16] + "..."
                _eprint(f"    {k}: {shown}")
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            resp = urllib.request.urlopen(req, context=self.ctx)
            body = resp.read()
            if self.verbose:
                _eprint(f"<-- {resp.status} ({len(body)} bytes)")
            return resp.status, dict(resp.headers), body
        except urllib.error.HTTPError as e:
            body = e.read()
            if self.verbose:
                _eprint(f"<-- {e.code} {e.reason} ({len(body)} bytes)")
            return e.code, dict(e.headers), body

    def login(self, user, password):
        payload = json.dumps({
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "grant_type": "password", "username": user, "password": password,
        }).encode()
        status, _, body = self._request(
            "POST", "/token",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            data=payload, auth=False)
        if status != 200:
            raise SystemExit(f"login failed (HTTP {status}): {body.decode('utf-8', 'replace')}")
        tok = json.loads(body)
        # CSWeb 8.0 returns the token at the top level; CSWeb 8.1 nests it:
        # {"user": {"id", "roleName"}, "credentials": {"access_token", ...}}
        creds = tok.get("credentials") if isinstance(tok.get("credentials"), dict) else tok
        self.token = creds.get("access_token")
        if not self.token:
            raise SystemExit(f"login response had no access_token: {body.decode('utf-8','replace')}")
        return tok


def _enc(seg):
    """Percent-encode a single path segment (dict name, case id, username)."""
    return urllib.parse.quote(seg, safe="")


def _enc_path(p):
    """Encode a multi-segment file/folder path, keeping the slashes."""
    return urllib.parse.quote(p.lstrip("/"), safe="/")


def _out(status, headers, body, args, expect_json=True):
    """Print a response; write binary/text to --out if given. Return exit code."""
    outfile = getattr(args, "out", None)
    if outfile:
        with open(outfile, "wb") as f:
            f.write(body)
        _eprint(f"HTTP {status}; wrote {len(body)} bytes -> {outfile}")
        return 0 if 200 <= status < 300 else 1
    text = body.decode("utf-8", "replace")
    if getattr(args, "raw", False) or not expect_json:
        sys.stdout.write(text)
        if text and not text.endswith("\n"):
            sys.stdout.write("\n")
    else:
        try:
            print(json.dumps(json.loads(text), indent=2, ensure_ascii=False))
        except (ValueError, json.JSONDecodeError):
            sys.stdout.write(text + ("\n" if not text.endswith("\n") else ""))
    if not (200 <= status < 300):
        _eprint(f"(HTTP {status})")
        return 1
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="csweb.py",
        description="Minimal CLI for a CSPro/CSWeb sync server (see reference/csweb-sync-api.md).")
    p.add_argument("--url", default=os.environ.get("CSWEB_URL"),
                   help="server base URL (e.g. https://csweb.example.org). $CSWEB_URL")
    p.add_argument("--user", default=os.environ.get("CSWEB_USER"), help="$CSWEB_USER")
    p.add_argument("--password", default=os.environ.get("CSWEB_PASSWORD"), help="$CSWEB_PASSWORD")
    p.add_argument("--token", default=os.environ.get("CSWEB_TOKEN"),
                   help="reuse an existing bearer token instead of logging in. $CSWEB_TOKEN")
    p.add_argument("--insecure", action="store_true", help="skip TLS certificate verification")
    p.add_argument("--raw", action="store_true", help="print response body verbatim")
    p.add_argument("-v", "--verbose", action="store_true", help="trace requests to stderr")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login")
    sub.add_parser("server")
    sub.add_parser("dicts")

    sp = sub.add_parser("dict-get"); sp.add_argument("name"); sp.add_argument("--out")
    sp = sub.add_parser("dict-add"); sp.add_argument("file")
    sp = sub.add_parser("dict-delete"); sp.add_argument("name")

    sp = sub.add_parser("cases")
    sp.add_argument("name")
    sp.add_argument("--universe"); sp.add_argument("--count", type=int)
    sp.add_argument("--start-after"); sp.add_argument("--device"); sp.add_argument("--if-match")

    sp = sub.add_parser("case-get"); sp.add_argument("name"); sp.add_argument("caseid"); sp.add_argument("--out")

    sp = sub.add_parser("syncs")
    sp.add_argument("name")
    sp.add_argument("--from", dest="from_"); sp.add_argument("--to")
    sp.add_argument("--device"); sp.add_argument("--limit", type=int); sp.add_argument("--offset", type=int)

    sub.add_parser("apps")
    sp = sub.add_parser("app-get"); sp.add_argument("name"); sp.add_argument("--out", required=True)

    sub.add_parser("users")
    sp = sub.add_parser("user-get"); sp.add_argument("name")

    sp = sub.add_parser("file"); sp.add_argument("path"); sp.add_argument("--out")
    sp = sub.add_parser("file-info"); sp.add_argument("path")
    sp = sub.add_parser("folder"); sp.add_argument("path")

    args = p.parse_args(argv)
    if not args.url:
        raise SystemExit("error: --url (or $CSWEB_URL) is required")

    c = CSWeb(args.url, token=args.token, insecure=args.insecure, verbose=args.verbose)

    # Authenticate unless a token was supplied or we're only fetching one.
    if args.cmd != "login" and not c.token:
        if not (args.user and args.password):
            raise SystemExit("error: need --user/--password (or --token, or $CSWEB_* env vars)")
        c.login(args.user, args.password)

    if args.cmd == "login":
        if not (args.user and args.password):
            raise SystemExit("error: login needs --user and --password")
        tok = c.login(args.user, args.password)
        print(json.dumps(tok, indent=2))
        return 0

    if args.cmd == "server":
        return _out(*c._request("GET", "/server"), args)

    if args.cmd == "dicts":
        return _out(*c._request("GET", "/dictionaries"), args)

    if args.cmd == "dict-get":
        st, h, b = c._request("GET", f"/dictionaries/{_enc(args.name)}",
                              headers={"Accept": "text/plain"})
        return _out(st, h, b, args, expect_json=False)

    if args.cmd == "dict-add":
        with open(args.file, "rb") as f:
            data = f.read()
        # The TRAILING SLASH is required.  CSWeb is a Symfony app and its
        # add-dictionary route is declared as "/dictionaries/"; Symfony only
        # redirects a slash mismatch for safe methods, so "POST /dictionaries"
        # (no slash) comes back as a plain 404 HTML page while
        # "GET /dictionaries" on the very same path works fine.  Do not
        # "tidy" this slash away.
        return _out(*c._request("POST", "/dictionaries/",
                               headers={"Content-Type": "text/plain"}, data=data), args)

    if args.cmd == "dict-delete":
        return _out(*c._request("DELETE", f"/dictionaries/{_enc(args.name)}"), args)

    if args.cmd == "cases":
        h = {"Accept": "application/json"}
        if args.universe:    h["x-csw-universe"] = args.universe
        if args.count is not None: h["x-csw-case-range-count"] = str(args.count)
        if args.start_after: h["x-csw-case-range-start-after"] = args.start_after
        if args.device:      h["x-csw-device"] = args.device
        if args.if_match:    h["If-Match"] = args.if_match
        st, hd, b = c._request("GET", f"/dictionaries/{_enc(args.name)}/cases", headers=h)
        rng = hd.get("x-csw-case-range-count")
        if rng:
            _eprint(f"x-csw-case-range-count: {rng}   (returned/total)")
        etag = hd.get("etag") or hd.get("ETag")
        if etag:
            _eprint(f"etag: {etag}   (pass as --if-match next time for deltas)")
        return _out(st, hd, b, args)

    if args.cmd == "case-get":
        st, h, b = c._request(
            "GET", f"/dictionaries/{_enc(args.name)}/cases/{_enc(args.caseid)}",
            headers={"Accept": "application/json"})
        return _out(st, h, b, args)

    if args.cmd == "syncs":
        q = {}
        if args.from_:  q["from"] = args.from_
        if args.to:     q["to"] = args.to
        if args.device: q["deviceId"] = args.device
        if args.limit is not None:  q["limit"] = args.limit
        if args.offset is not None: q["offset"] = args.offset
        qs = ("?" + urllib.parse.urlencode(q)) if q else ""
        return _out(*c._request("GET", f"/dictionaries/{_enc(args.name)}/syncs{qs}"), args)

    if args.cmd == "apps":
        return _out(*c._request("GET", "/apps"), args)

    if args.cmd == "app-get":
        st, h, b = c._request("GET", f"/apps/{_enc(args.name)}",
                             headers={"Accept": "application/octet-stream"})
        return _out(st, h, b, args)

    if args.cmd == "users":
        return _out(*c._request("GET", "/users"), args)

    if args.cmd == "user-get":
        return _out(*c._request("GET", f"/users/{_enc(args.name)}"), args)

    if args.cmd == "file":
        st, h, b = c._request("GET", f"/files/{_enc_path(args.path)}/content")
        return _out(st, h, b, args, expect_json=False)

    if args.cmd == "file-info":
        return _out(*c._request("GET", f"/files/{_enc_path(args.path)}"), args)

    if args.cmd == "folder":
        return _out(*c._request("GET", f"/folders/{_enc_path(args.path)}"), args)

    p.error(f"unknown command {args.cmd}")


if __name__ == "__main__":
    sys.exit(main())
