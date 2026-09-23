# CSWeb sync server — REST API reference

CSPro devices (CSEntry) sync collected data and app packages to a **CSWeb**
server over a REST API ("CSPro Sync", API v2.0). This is how you inspect what's
actually on the server: which dictionaries exist, how many cases each holds,
the sync history, deployed app packages, files/folders, and users.

Full machine-readable spec: [`csweb-swagger.json`](csweb-swagger.json) (OpenAPI
2.0). Helper CLI: [`../scripts/csweb.py`](../scripts/csweb.py) — stdlib-only,
one subcommand per endpoint.

- **Base path:** every route is under `{server}/api` (e.g. `https://csweb.example.org/api/dictionaries`).
- **API version:** 2.0.0. `GET /server` returns the running server's `apiVersion`.

## Authentication — OAuth2 password grant

Only `POST /api/token` is unauthenticated. **Everything else — including
`/server` — requires a bearer token.**

```
POST {server}/api/token
Content-Type: application/json

{"client_id":"cspro_android","client_secret":"cspro",
 "grant_type":"password","username":"<user>","password":"<pass>"}
```

`client_id`/`client_secret` are the fixed CSWeb defaults (`cspro_android` /
`cspro`) baked into every CSWeb install's `oauth_clients` table and used by both
the web login and the Android client — they are **not** per-user secrets. The
user is a real CSWeb account. Response:

```json
{ "access_token": "…", "token_type": "Bearer",
  "expires_in": 3600, "refresh_token": "…", "scope": null }
```

**CSWeb 8.1 (apiVersion 3.0) nests the token** and adds the user's role:
`{"user": {"id", "roleName"}, "credentials": {"access_token", "token_type",
"expires_in", "refresh_token", "scope"}}`. `csweb.py` reads both shapes.
Other 8.1 differences seen on a live pair of servers (2026-09-23): `/dictionaries`
entries add `dictionaryName` + `modifiedTime`; `/users` entries drop 8.0's stray
numeric keys (`"0"`..`"5"`) and add `uuid`; an unknown route returns **500
"Internal Server Error"** instead of 8.0's 404 HTML page, so on 8.1 a misspelled
route looks like a server crash. `GET /dictionaries/{name}/syncs` is still an
unimplemented stub on both ("How about implementing getSyncHistory as a GET
method ?"). 8.1 is a fresh install beside 8.0 (no upgrade path): users, data and
files do not carry over, and CSEntry 8.0 cannot sync with it. Full list in
"CSWeb 8.1 (REST API 3.0)" below.

Then send `Authorization: Bearer <access_token>` on every subsequent request.
When it expires, either re-POST `/token` with the same credentials, or use
`grant_type=refresh_token` + `refresh_token=<…>` (the server also supports the
refresh-token grant and issues a fresh refresh token each time).

> The token body is sent as **JSON** with `Content-Type: application/json` — that
> is exactly what the CSWeb web login and Android client send, so it is the
> proven path. If a particular server build rejects JSON, retry the token call
> form-encoded (`application/x-www-form-urlencoded`) with the same four fields.

## Endpoints

| Method & path | Purpose | `csweb.py` |
|---|---|---|
| `GET /server` | Server `deviceId` + `apiVersion` | `server` |
| `GET /dictionaries` | List dicts: `name`, `label`, `caseCount` | `dicts` |
| `POST /dictionaries/` | Upload a new `.dcf` (body = dict text, `text/plain`). **Trailing slash required** - see below | `dict-add FILE` |
| `GET /dictionaries/{name}` | Download the `.dcf` text | `dict-get NAME` |
| `DELETE /dictionaries/{name}` | Delete dict **and all its cases** | `dict-delete NAME` |
| `GET /dictionaries/{name}/cases` | List/download cases (JSON) | `cases NAME` |
| `POST /dictionaries/{name}/cases` | Add/update cases (JSON body) | — |
| `GET …/cases/{caseId}` | One case by GUID (JSON or text) | `case-get NAME ID` |
| `PUT/DELETE …/cases/{caseId}` | Overwrite / delete a case | — |
| `GET /dictionaries/{name}/syncs` | Sync history log | `syncs NAME` |
| `GET /files/{path}` | File metadata (`FileInfo`) | `file-info PATH` |
| `GET /files/{path}/content` | Download file bytes | `file PATH --out F` |
| `PUT /files/{path}/content` | Upload file (needs `Content-MD5`) | — |
| `GET /folders/{path}` | List files in a server folder | `folder PATH` |
| `GET /apps` | List deployable app packages (`.csapb`) | `apps` |
| `GET /apps/{name}` | Download an app package (binary) | `app-get NAME --out F` |
| `PUT/DELETE /apps/{name}` | Upload / delete an app package | — |
| `GET /users`, `GET /users/{name}` | List users / one user | `users`, `user-get NAME` |
| `PUT/DELETE /users/{name}` | Update / delete a user | — |

### Slashes, part 2: `/folders/{path}` takes the path WITHOUT a leading slash

The folder listing is the mirror image of the rule above - here a leading slash
is what breaks it:

```
GET /folders/apps        -> 200, the listing
GET /folders//apps       -> 404 {"code":"directory_not_found"}
```

`csweb.py folder PATH` passes PATH through verbatim, so call it as
`folder apps` / `folder apps/LMS/Menu`, never `folder /apps`. The 404 body here
IS the API's JSON envelope (`directory_not_found`), not a Symfony HTML page -
that difference is how you tell "wrong path spelling" from "route not matched".

### The trailing slash on `POST /dictionaries/` (verified the hard way)

Uploading a dictionary **only works with a trailing slash**:

```
POST {server}/api/dictionaries/     -> 200 {"code":200,"description":"Success"}
POST {server}/api/dictionaries      -> 404 (Symfony "Oops! An Error Occurred" HTML page)
```

CSWeb is a Symfony application and declares this route as `/dictionaries/`.
Symfony auto-redirects a trailing-slash mismatch **only for safe methods**, so
`GET /dictionaries` (no slash) works fine and lists the dictionaries, while
`POST /dictionaries` on that identical path 404s. The 404 therefore looks like
"this server build has no upload endpoint" when the route is really there.

Tell-tale signs it is the slash and not auth or the build:
- the response body is an **HTML** Symfony error page, not the API's JSON
  `{"code":...,"description":...}` envelope;
- `GET` on the same path succeeds with the same bearer token;
- `DELETE /dictionaries/{name}` (no trailing slash - a different route) succeeds too.

Note the swagger's `basePath` is `"/api/"`, with the slash already on it; that
is the hint the route table is slash-terminated. Other verbs are unaffected:
`PUT /dictionaries/{name}` returns 405 (no such method) and only the collection
route needs the slash.

This matters because there is no other API way back: once
`DELETE /dictionaries/{name}` has removed a dictionary, re-registering it is
either this POST or a full CSDeploy run (which also pushes the app package to
field devices).

(The mutating case/file/user endpoints are documented here for completeness but
intentionally **not** wired into the CLI — see "Safety" below.)

### Cases: paging & deltas (headers, not query params)

`GET …/cases` is controlled by request **headers**, and reports progress in
response headers:

- `x-csw-universe` — filter to a universe (a CSPro universe expression, e.g. a
  region/EA prefix) so you only pull matching cases.
- `x-csw-case-range-count` (request) — max cases to return; server replies
  `206 Partial Content` when more remain.
- `x-csw-case-range-start-after` — a case GUID; returns only cases whose GUID is
  alphabetically greater. This is the paging cursor: pass the last GUID you got.
- `x-csw-case-range-count` (**response**) — `x/y` = returned / total matching.
- `etag` (response) — a server revision marker. Send it back as `If-Match` on the
  next call to get **only** cases added/changed since. Server returns `412` if it
  doesn't recognise the etag (revision pruned) — then re-pull without `If-Match`.
- `x-csw-exclude-revisions` — skip cases from listed revisions (a device avoiding
  re-downloading what it just uploaded).

Case JSON: `id` (GUID), `caseids` (concatenated key-field values), `label`,
`level-1` (collected data as a JSON string; older servers use `data` = array of
CSPro text records), `notes`, `deleted`, `verified`, `partialSave`, `clock`
(vector clock for conflict resolution). See `Case` in the swagger.

### Sync history

`GET …/syncs?from=&to=&deviceId=&limit=&offset=` — each entry is
`{deviceId, dictionary, direction: put|get|both, universe, dateTime}`. Dates are
RFC3339 (`1985-04-12T23:20:50.52Z`). Good for "which devices synced what, when".

## Using the CLI

```bash
# creds inline …
python scripts/csweb.py --url https://csweb.example.org \
    --user admin --password secret dicts

# … or via env, so the password isn't in argv / shell history:
export CSWEB_URL=https://csweb.example.org CSWEB_USER=admin CSWEB_PASSWORD=secret
python scripts/csweb.py dicts                       # list dictionaries + case counts
python scripts/csweb.py server                      # server deviceId + apiVersion
python scripts/csweb.py dict-get UNHS2026_Staff --out staff.dcf
python scripts/csweb.py cases UNHS2026 --count 50    # first 50 cases (note etag/range on stderr)
python scripts/csweb.py cases UNHS2026 --universe "1" --count 200
python scripts/csweb.py syncs UNHS2026 --limit 100   # who synced, when
python scripts/csweb.py apps                          # deployed app packages
python scripts/csweb.py folder UNHS2026              # server-side files for an app
python scripts/csweb.py login                        # just print a token to reuse via --token
```

Reuse a token instead of re-authenticating each call:
`export CSWEB_TOKEN=$(python scripts/csweb.py login --raw | python -c "import json,sys;print(json.load(sys.stdin)['access_token'])")`.

Flags: `--insecure` (skip TLS verify, e.g. self-signed dev server), `--raw` (print
body verbatim, no pretty-print), `-v` (trace request/response lines to stderr).
Exit code is `0` only on HTTP 2xx.

## How this ties into the rest of the toolkit

- A dictionary **downloaded** from the server (`dict-get`) is the same `.dcf`
  you lint locally — run [`cspro_lint.py`](../scripts/cspro_lint.py) on it to check
  it against the app's logic/forms, or diff it against the project's `.dcf` to spot
  server-vs-local drift (e.g. the `UNHS2024_*` vs `UNHS2026_*` naming drift the
  linter's pff check already warns about).
- `caseCount` per dictionary and the sync history are the quickest read on
  whether field devices are actually reaching the server and how much data has
  landed — no database access required.
- App packages (`/apps`) are the deployed `.csapb` bundles; `folder`/`file` expose
  the server's file store (the same files CSEntry pulls on sync).

## Inspecting an Android device directly (adb)

CSEntry stores everything under:

```
/sdcard/Android/data/gov.census.cspro.csentry/files/csentry/
    Packages/<APP>.zip          the deployed package as downloaded
    <APP>/Menu/Menu.pen         the running compiled app
    <APP>/Data/*.csdb           the data files (SQLite)
    sync.log                    every sync attempt, with errors
```

In Git Bash prefix adb with `MSYS_NO_PATHCONV=1`, or paths like `/sdcard/...` get
rewritten into Windows paths and `adb pull` fails with "No such file or directory".

```bash
export MSYS_NO_PATHCONV=1
adb pull /sdcard/Android/data/gov.census.cspro.csentry/files/csentry/APP/Data/X.csdb .
```

A `.csdb` is plain SQLite. The tables that matter for diagnosing a sync:

| table | what it tells you |
|---|---|
| `cases` | `id` (GUID), `key`, `deleted`, `last_modified_revision` |
| `vector_clock` | `case_id, device, revision` — **the reason a case did or didn't update** |
| `sync_history` | timestamp, `universe`, direction, server revision per sync |
| `meta` | the dictionary the file was written with (JSON) — confirms schema migrations |

**The silent-skip trap.** CSPro only takes the server's copy when the server's clock
dominates the device's — i.e. server revision >= device revision for *every* device entry.
If the device holds a local write, it has an entry the server lacks, so the server can
never dominate and the case is skipped **with no error**. The device just keeps its old
data while every other case updates normally, which makes it look like "sync works but
this one record is stale".

Diagnose by joining the two clocks and listing entries the device has that the server
doesn't. Fixes, in increasing order of blast radius: (1) delete the device's `.csdb` and
re-sync; (2) re-POST the case with a clock that also covers the device's entries; (3) the
GUID/tombstone method — tombstone the old GUID and POST the data under a fresh GUID, which
every device accepts unconditionally because it has no history for it (this discards local
edits everywhere, so it is a data decision, not just a technical one).

## Deploying an app, and the stale-unzip trap

Editing `.apc` / `.qsf` / `.fmf` does **not** change the app. `.pen` is a compiled
binary that only the **CSPro Deploy tool (CSDeploy)** produces. The normal flow is:

1. CSDeploy builds the `.pen` files and uploads a **zip package** to the server
   (visible as `GET /apps/{NAME}`, with a `buildTime`).
2. Someone **unzips that package** into the server's file store at `/apps/{NAME}/…`.
3. Devices pull **individual files from the unzipped tree** — the app's own
   `syncfile(GET, "/apps/{NAME}/Menu/Menu.pen", …)` logic — **not** from the package.

Step 2 is the trap: a successful deploy updates the package while the unzipped tree
stays stale, so `syncfile` finds nothing newer and devices silently keep the old
build. Symptom: "I deployed but the tablets still show the old version number."

Diagnose by comparing the two, which must agree:

```bash
python scripts/csweb.py apps                  # package buildTime for the app
python scripts/csweb.py folder "apps/APP/Menu"  # lastModified of the unzipped .pen
```

If `buildTime` is newer than the unzipped file's `lastModified`, the unzip never ran.
For a definitive check, download the package and byte-compare every member against
`/files/apps/{NAME}/{path}/content` (URL-quote paths — some contain spaces).

`.pen` files are **bzip2** (`BZh9` magic) and their strings are **UTF-16LE**, so you
can read a build's identity without any CSPro tooling:

```python
import bz2, re
d = bz2.decompress(open('Menu.pen','rb').read())
print(sorted({m.group().decode('utf-16-le')          # version banner text
              for m in re.finditer(rb'1\x00\.\x000\x00\.\x00[0-9]\x00', d)}))
print('N_TRIP'.encode('utf-16-le') in d)             # is a given change present?
```

Caveat: the compiler **strips comments**, so only string literals, identifiers and
question text survive. A logic change that adds no new literal cannot be detected
this way — fall back to comparing the build timestamp against the source mtime.

Note that dictionaries live in a **separate registry** (`/dictionaries`) from the file
store, so confirm dictionary changes there rather than assuming a deploy carried them.

## CSWeb 8.1 (REST API 3.0)

Source of truth: the open-source CSWeb repo, github.com/csprousers/csweb (tags
`v8.0.1-2024-03-19`, `v8.1.0-2026-06-16`, `v8.1.2-2026-07-09`; 8.1.0 -> 8.1.2
touched only `DictionaryHelper.php`). `src/version.php` holds `API_VERSION '3.0'`.
The bundled swagger (`src/CSPro/swagger.json`, copied here as
`csweb81-swagger.json`) was NOT fully updated: it still says 2.0.0 and has the
same 13 paths as 8.0 - only its `Case`, `Dictionary` and `User` models changed.
What the code adds on top (checked 2026-09-23):

- **Login** `POST /token`: response nested as `{user:{id,roleName},credentials:{...}}`.
  The username is checked against `^[a-zA-Z0-9_\-]{4,64}$` BEFORE the password -
  anything else (dots, spaces, 3 characters) gets `400 invalid_request`.
- **New routes:** `GET /dictionaries/{name}/metadata` (key structure, max sync
  revision, and the caller's effective permissions on that dictionary);
  `GET /dictionaries/{name}/binary-data/{signature}` (binary dictionary items -
  images/audio - by MD5 signature); `POST /messages/` (a device message
  `{"timestamp","name","value"}` + header `x-csw-device`, stored in
  `cspro_messages`; sent by the 8.1 Action Invoker `Sync.sendMessage`).
  No route was removed.
- **Case JSON:** `id`->`uuid`, `caseids`->`key`, `data`->`level-1` (object), plus
  `caseNote`. Scripts that parse `GET .../cases` need updating.
- **`/dictionaries`:** `name` (the syncable name) + `dictionaryName` (the .dcf
  name); they can differ, since 8.1 lets one dictionary sync to several datasets.
- **Roles** (UI only - there is no roles REST route): 1 Standard User,
  2 Administrator, 3 Developer (new). Permissions: `data.read/.write/.clear/
  .clear.dashboard`, `apps`, `users`, `roles`, `reports`, `settings`, `paradata`,
  `dictionaries`, `messages`, `files` (each `.read`/`.write`), `login` (dashboard).
  Standard User = data r/w, apps.read, dictionaries.read, files r/w, paradata r/w,
  messages r/w - enough for sync, syncapp and syncfile, but it cannot deploy
  (`apps.write`) or log in to the dashboard. Role defaults cover dictionaries
  added later unless overridden per dictionary. **Role id 3 was free for custom
  roles in 8.0 and is Developer in 8.1** - map custom roles by name, not id, when
  moving users.
- **File uploads** (`FileSecurityValidator`): the API (`PUT /files/.../content`,
  syncfile) blocks only executables (exe, bat, sh, php... and executable MIME
  types). The web dashboard file manager also rejects web-executable types (html,
  js, svg) and any **MIME/extension mismatch**; SQLite MIME maps only to
  `.sqlite3`, so `.mbtiles` / `.csdb` uploads through the dashboard are expected to
  be refused as "spoofing" - upload map tiles through the API or syncfile.

## Safety

- Treat the server as **live production data**. The CLI deliberately exposes only
  read endpoints plus `dict-add` — it does **not** implement case `POST/PUT/DELETE`,
  `dict-delete` is present but destructive (**deletes every case in the dict**),
  and file/app uploads are left out. Don't add write paths casually.
- Never hard-code or commit real credentials. Prefer the `CSWEB_*` env vars over
  `--password` on the command line (argv is visible in process lists / shell history).
- Confirm the target `--url` before any mutating call — dev vs production CSWeb
  servers can look identical.
