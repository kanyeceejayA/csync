# The CSWeb sync API, in short

The machine-readable spec is [`csweb-swagger.json`](csweb-swagger.json) (OpenAPI 2.0,
"CSPro Sync" 2.0). Open [`api.html`](api.html) in a browser to read it as Swagger UI, or
visit **http://127.0.0.1:8800/api-docs** while the web front end is running.

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
The answer carries `access_token`, `expires_in` and `refresh_token`; send
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
| `If-Match` + `etag` | both | only what changed since that etag; `412` = etag forgotten, pull fully |
| `x-csw-exclude-revisions` | request | skip revisions the caller already has |

`csync` keeps the etag per dictionary in the local store, so `sync_down` is a delta pull
by default and silently falls back to a full pull on `412`.

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
- **The start-after cursor can loop** on some builds instead of advancing. `CSWeb.cases()`
  stops when a page does not move the cursor, and uses one large range by default.
- **Vector clocks decide who wins.** A device accepts the server's copy only when the
  server's clock dominates its own for *every* device entry. Push with a bumped clock
  (`bump_clock`), and use `boost=True` only when you intend to overwrite a tablet's edit.

## CSWeb 8.0 vs 8.1 — does this client need to change?

**No.** The sync REST API is the same in both: same base path, same OAuth2 password grant
with the same `cspro_android` / `cspro` client, the same dictionary and case routes, and
the same header-based paging. `csync` runs against either unchanged, and the swagger here
describes both.

Two 8.1 differences are worth knowing anyway:

1. **Usernames are validated at `/token`.** CSWeb 8.1's API controller rejects anything
   that does not match `^[a-zA-Z0-9_\-]{4,64}$` with `400 invalid_request` —
   "Invalid username format." A username with a dot or an `@`, or shorter than four
   characters, signs in on 8.0 and fails on 8.1. If a working `CSWEB_USER` starts failing
   after a server upgrade, this is why.
2. **The server database changed** (MySQL 8 is now the minimum; some internal tables and
   columns were renamed, e.g. `cspro_dictionaries.name`, `last_case_uuid`, plus a new
   `cspro_messages` table). None of it is visible over the API — it only matters if you
   query the CSWeb database directly.

On the file side, CSPro 8.0 and 8.1 both write JSON `.dcf` dictionaries and `.csdb` files
at schema version 3, which is what `csync/csdb.py` reads. `csdb.schema_info()` reports the
schema and CSPro version of any file, so a future bump is easy to spot.
