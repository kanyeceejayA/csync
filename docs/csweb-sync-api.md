# The CSWeb sync API, in short

The machine-readable specs are [`csweb-swagger.json`](csweb-swagger.json) (CSWeb 8.0,
REST API 2.0) and [`csweb81-swagger.json`](csweb81-swagger.json) (CSWeb 8.1.2, REST API
3.0 - the file still says 2.0.0 and omits what 8.1 added; see the last section). Open
[`api.html`](api.html) in a browser to read either as Swagger UI, or visit
**http://127.0.0.1:8800/api-docs** while the web front end is running.

This page is the short version: what `csync/client.py` implements, and the quirks that
cost real time to discover.

## Authentication

Everything lives under `{server}/api`. Only `POST /api/token` is unauthenticated —
`/server` needs a token too.

```http
POST {server}/api/token
Content-Type: application/json

{"client_id":"cspro_android","client_secret":"cspro",
 "grant_type":"password","username":"<user>","password":"<pass>"}
```

`cspro_android` / `cspro` are the fixed client credentials every CSWeb install ships with
(they are in the `oauth_clients` table and in the Android client) — not per-user secrets.
CSWeb 8.0 answers `{access_token, expires_in, refresh_token, ...}`; CSWeb 8.1 nests the
same fields: `{"user": {"id", "roleName"}, "credentials": {access_token, ...}}`. Send
`Authorization: Bearer <access_token>` on every other call.

## Endpoints

| Method & path | Purpose | In `csync` |
|---|---|---|
| `GET /server` | `deviceId`, `apiVersion` | `CSWeb.server()` |
| `GET /dictionaries` | names, labels, `caseCount` | `CSWeb.dictionaries()` |
| `POST /dictionaries/` | upload a `.dcf` (**trailing slash required**) | `CSWeb.add_dictionary()` |
| `GET /dictionaries/{name}` | download the `.dcf` text | `CSWeb.dictionary_text()` |
| `DELETE /dictionaries/{name}` | delete the dictionary **and all its cases** | `CSWeb.delete_dictionary()` |
| `GET /dictionaries/{name}/cases` | list/download cases | `CSWeb.cases()` |
| `POST /dictionaries/{name}/cases` | add or update cases (JSON array) | `CSWeb.post_cases()` |
| `GET/PUT/DELETE …/cases/{guid}` | one case | `CSWeb.case()`, `CSWeb.delete_case()` |
| `GET /dictionaries/{name}/syncs` | sync history | `CSWeb.syncs()` |
| `GET /files/{path}`, `…/content` | file metadata, bytes | `CSWeb.file_info()` |
| `GET /folders/{path}` | folder listing (**no leading slash**) | `CSWeb.folder()` |
| `GET /apps` | deployed application packages | `CSWeb.apps()` |
| `GET /users` | accounts | — |

## Cases page through headers, not query parameters

| Header | Direction | Meaning |
|---|---|---|
| `x-csw-case-range-count` | request | maximum cases to return |
| `x-csw-case-range-count` | response | `returned/total` |
| `x-csw-case-range-start-after` | request | GUID cursor — return cases whose GUID sorts after it |
| `x-csw-universe` | request | a CSPro universe (usually a case-key prefix) |
| `x-csw-if-revision-exists` | request | only cases changed after this server revision; `412` = the server does not know it (reset), pull fully |
| `ETag` / `x-csw-chunk-max-revision` | response | the revision this page reached (nginx strips `ETag`, hence the custom header) |
| `x-csw-exclude-revisions` | request | skip revisions the caller already has |

The server never reads `If-Match`. To page, send the next request with
`x-csw-case-range-start-after: <last GUID>` **and** `x-csw-if-revision-exists: <the page's
chunk max revision>`: the server selects `(revision = R and uuid > G) or revision > R`, so
the GUID on its own (R = 0) serves the first page again. `csync` keeps the revision per
dictionary in the local store, so `sync_down` is a delta pull by default and silently falls
back to a full pull on `412`.

## The quirks worth knowing

- **`POST /dictionaries/` needs its trailing slash.** CSWeb is a Symfony app and the
  collection route is slash-terminated; Symfony only auto-redirects a slash mismatch for
  safe methods. `GET /dictionaries` works, `POST /dictionaries` returns a **404 HTML page**.
  An HTML body instead of the `{"code","description"}` envelope is the tell.
- **`GET /folders/{path}` breaks on a *leading* slash** — `folders/apps`, never `folders//apps`.
- **A single-occurrence record must be sent as an object**, not a one-element array.
  CSWeb answers `200` either way and then stores the case wrong. `Dictionary.to_level1()`
  gets this right; `sync_up` reads the case back afterwards because 200 is not proof.
- **Hard `DELETE` of a case is unreliable on some builds.** Delete by tombstone
  (`deleted: true`) — that is also what tablets need in order to drop the case.
- **The start-after cursor "loops" when sent alone** - see the paging rule above. Sent
  with the page's revision it advances; `CSWeb.cases()` does that, and still stops if a
  page does not move the cursor.
- **Vector clocks decide who wins.** A device accepts the server's copy only when the
  server's clock dominates its own for *every* device entry. Push with a bumped clock
  (`bump_clock`), and use `boost=True` only when you intend to overwrite a tablet's edit.

## CSWeb 8.0 vs 8.1 - two API generations

CSWeb 8.1 is REST API **3.0** (`src/version.php` in the open-source
[csprousers/csweb](https://github.com/csprousers/csweb): "incremented when REST
endpoints/arguments changes. Changed for 8.1"). A client written for 8.0 can neither sign
in to it nor send it a case. `csync` reads `apiVersion` from `GET /server` and speaks
whichever the server does, as CSEntry does (`CSWeb.case_api` = 2 or 3):

- **Sign-in:** the token answer is nested (see Authentication). The username is checked
  against `^[a-zA-Z0-9_\-]{4,64}$` *before* the password - a dot, an `@` or fewer than four
  characters gets `400 invalid_request` "Invalid username format."
- **Cases (V3):** `{"key", "uuid", "label", "deleted", "verified", "<LEVEL NAME>": {...},
  "clock"}`. `uuid`, `key` and `clock` are required (the upload is validated against the
  swagger's `Case` model; a V2 body fails). The level object is keyed by the dictionary's
  level name, holds the id items at its top, every record as an **array** of occurrences
  (even a single one) and every value as `{"code": value}`. 8.1 stores each uploaded case
  whole and returns it as sent. `Dictionary.case_body(api=3)` / `to_level_v3()` build it;
  `from_case()` reads both generations.
- **`GET /dictionaries`** adds `dictionaryName` (the .dcf name) beside `name` (the
  *syncable* name - 8.1 lets one dictionary sync to several datasets) and `modifiedTime`.
- **New routes:** `GET /dictionaries/{name}/metadata` (key structure, latest revision, the
  caller's permissions on it); `GET /dictionaries/{name}/binary-data/{signature}` (images or
  audio stored in cases, by MD5); `POST /messages/` (a device message
  `{"timestamp", "name", "value"}` with header `x-csw-device`, stored in `cspro_messages`).
- **Roles:** Standard User (data read/write, apps read, files, paradata, messages - no
  deploying, no dashboard), Administrator, and the new Developer (deploys apps, manages
  users). There is no roles route in the API.
- **Errors:** an unknown route answers `500 Internal Server Error` instead of 8.0's 404 page.
- **Unchanged:** the paging headers, `/files`, `/folders`, `/apps`; the sync-history route
  is still a stub on both ("How about implementing getSyncHistory as a GET method ?").
- **The server database changed** (MySQL 8 minimum, renamed columns, `cspro_messages`) -
  it only matters if you query the CSWeb database directly.

On the file side, CSPro 8.0 and 8.1 both write JSON `.dcf` dictionaries and `.csdb` files
at schema version 3, which is what `csync/csdb.py` reads. `csdb.schema_info()` reports the
schema and CSPro version of any file, so a future bump is easy to spot.
