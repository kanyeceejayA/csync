# csync

Sync **any** CSPro dictionary between a **CSWeb** server and a local SQLite store, from
Python — plus a small command line, a tiny web page, and an example assignments module.

```
CSEntry tablets  <--sync-->  CSWeb server  <--csync-->  local SQLite store  <-->  your code
                                  ^                            ^
                          docs/csweb-swagger.json       data/csync.db (plain SQLite)
```

The point: **a `.dcf` describes a survey completely**, so none of the core functions need to
know your field names. Give them a dictionary name and plain Python dicts and they work —
for assignments, staff lists, geocodes, a household questionnaire, anything.

- **Library** — `csync.add_case(...)`, `csync.sync_up(...)`, standard library only.
- **CLI** — `python -m csync pull|push|add|rm|ls|import-csdb ...`.
- **Web** — `python -m csync web`, one page, optional (Flask).
- **CSPro files** — read and write real `.csdb` data files.
- **API reference** — the CSWeb swagger spec ships in [`docs/`](docs/) with a viewer.

---

## Install

```bash
git clone <this repo> csync && cd csync
python -m pip install -r requirements.txt     # only for the web UI and the tests
cp .env.example .env                          # then edit it
```

Python 3.10+. The library itself needs **no** third-party packages; `pip install -e .`
also gives you a `csync` command instead of `python -m csync`.

## Configure

Everything comes from the environment, seeded from `.env` (see [`.env.example`](.env.example)):

| Variable | What it does |
|---|---|
| `CSWEB_URL` | `https://server/csweb/api` — the `/api` suffix is optional |
| `CSWEB_USER` / `CSWEB_PASSWORD` | a real CSWeb account |
| `CSWEB_DEVICE` | device id written into vector clocks — give each machine its own |
| `CSWEB_VERIFY_TLS` | `0` for a self-signed dev server |
| `CSYNC_STORE` | the local SQLite file (default `data/csync.db`) |
| `CSYNC_DICTS` | folder searched for `.dcf` files before asking the server |
| `CSYNC_BATCH` | cases per POST (200 is safe) |
| `CSYNC_WEB_HOST` / `CSYNC_WEB_PORT` | the optional web UI |

`.env` and `data/` are git-ignored. Nothing here is needed for the offline parts.

## Quick start — Python

```python
import csync

csync.dictionaries()                      # what the server holds, with case counts
csync.sync_down("MY_DICT")                # server -> local store

csync.add_case("MY_DICT", {"ID_A": 101, "ID_B": 7, "NAME": "Jane", "ROLE": "SUPERVISOR"})
csync.add_cases("MY_DICT", rows)          # many at once
csync.update_case("MY_DICT", "1010007", {"STATUS": 3})
csync.remove_case("MY_DICT", "1010007")   # tombstone, sent on the next push

csync.counts("MY_DICT")                   # {'total': 42, 'dirty': 3, ...}
csync.sync_up("MY_DICT")                  # local store -> server
```

A case is a **flat dict of item name → value**. Values may be given as value-set labels
(`"SUPERVISOR"` instead of `2`); they are coerced to the dictionary's type and length, and
an unknown item name is an error rather than silent data loss. Items of a repeating record
can be given at the top level (they become its first occurrence), or explicitly:

```python
{"ID_A": 101, "ROSTER_REC": [{"AGE": 30}, {"AGE": 7}]}
```

For several servers, an explicit store path, or tests, build a session instead of using the
module-level shortcuts:

```python
from csync import Session, Settings
s = Session(Settings(url="https://other/api", user="u", password="p", store="other.db"))
s.sync_down("MY_DICT")
```

## Quick start — command line

```bash
python -m csync check                        # is the configuration usable?
python -m csync dicts                        # dictionaries on the server
python -m csync pull  MY_DICT                # server -> store (delta by default)
python -m csync pull  MY_DICT --universe 101 # only cases whose key starts with 101
python -m csync ls    MY_DICT                # what is in the store ( * = not yet sent )
python -m csync add   MY_DICT ID_A=101 ID_B=7 NAME="Jane"
python -m csync add-csv MY_DICT rows.csv     # headers are item names
python -m csync rm    MY_DICT 1010007
python -m csync push  MY_DICT                # store -> server, then reads back to verify
python -m csync sync  MY_DICT                # pull then push

python -m csync register  dicts/MY_DICT.dcf  # upload a dictionary to the server
python -m csync diff      MY_DICT            # local .dcf vs the server's
python -m csync import-csdb data/MY.csdb     # load a CSPro data file into the store
python -m csync export-csdb MY_DICT out.csdb # write the store back out as a .csdb
python -m csync log                          # what this tool did recently
```

## The web front end

```bash
python -m csync web          # http://127.0.0.1:8800
```

One page — dictionaries down the left, cases on the right, `Sync down` / `Sync up` /
`Add case` across the top — with no build step and no framework. It is entirely optional
and calls exactly the same functions the library exposes. It lists the
dictionaries with their counts, shows the cases in the store (a dot marks one that has not
been sent), adds a case with a form **generated from the dictionary**, deletes a case, and
syncs either way. It also serves the CSWeb API reference at
**http://127.0.0.1:8800/api-docs**.

## The CSWeb API reference (bundled)

- **[`docs/api.html`](docs/api.html)** — Swagger UI for the spec. Open the file, or visit
  `/api-docs` while the web UI is running.
- **[`docs/csweb-swagger.json`](docs/csweb-swagger.json)** — the full OpenAPI 2.0 spec
  ("CSPro Sync" 2.0), the same API CSEntry itself uses.
- **[`docs/csweb-sync-api.md`](docs/csweb-sync-api.md)** — the short version: auth, every
  endpoint, the header-based paging, and the quirks that cost time to find (the trailing
  slash on `POST /dictionaries/`, HTML error bodies, the cursor that loops, tombstones
  instead of `DELETE`).

## CSPro / CSWeb 8.0 vs 8.1 — anything to change?

**No.** The sync REST API is identical in 8.0 and 8.1: same base path, same OAuth2 password
grant with the same built-in `cspro_android` / `cspro` client, same dictionary and case
routes, same paging headers. `csync` runs against either unchanged, and the bundled swagger
describes both. Same for the files: both write JSON `.dcf` dictionaries and `.csdb` files at
schema version 3, which is what `csync/csdb.py` reads (`csdb.schema_info()` reports the
schema of any file, so a future change is easy to spot).

Two 8.1 differences are worth knowing:

1. **8.1 validates the username at `/token`** against `^[a-zA-Z0-9_\-]{4,64}$` and answers
   `400 invalid_request` — "Invalid username format." A username containing a dot or `@`, or
   shorter than four characters, works on 8.0 and fails on 8.1. If sign-in breaks right after
   a server upgrade, this is the first thing to check.
2. **The CSWeb database changed** — MySQL 8 is now the minimum, some internal tables and
   columns were renamed, and there is a new `cspro_messages` table. Invisible over the API;
   it only matters if you query the CSWeb database directly.

## How syncing works (the five things worth knowing)

1. **The case key.** CSPro identifies a case by its id items concatenated into a fixed-width
   string (`caseids`), zero-filled exactly as the dictionary says: district `101` + EA `7`
   → `"10100007"`. `csync` builds it for you; it is also the key you pass to `update_case`,
   `remove_case` and `show`.
2. **Dirty vs clean.** Each stored case carries the content hash the server confirmed and
   the hash it has now. Different ⇒ dirty ⇒ `sync_up` will send it. A pull sets both; a
   verified push sets the server one.
3. **Pulls keep your unsent edits.** `sync_down` never silently overwrites a dirty case — it
   keeps yours and reports the key under `conflicts` if the server also changed.
4. **Vector clocks decide who wins.** A tablet accepts the server's copy only when the
   server's clock dominates its own. `sync_up` bumps the clock for `CSWEB_DEVICE`; if a
   tablet is holding a competing edit and must be overruled, `sync_up(..., boost=True)`
   raises every entry so the tablet cannot win. That discards what the tablet held, so it is
   a data decision, not a technical one.
5. **Deletes are tombstones.** `remove_case` marks `deleted: true` and pushes that — a hard
   `DELETE` is unreliable on some CSWeb builds and leaves tablets holding the case. A case
   that never reached the server is simply dropped locally.

Pushes are verified by reading the cases back: CSWeb answers `200` even when it has stored a
malformed case, so a `200` is not proof. `sync_up` reports anything it could not confirm
under `failed`.

## Working with CSPro `.csdb` files

A `.csdb` is a SQLite database with the dictionary embedded in it, so reading one needs
nothing else:

```python
from csync import csdb

csdb.schema_info("Assignments.csdb")      # {'schema_version': 3, 'cases': 1319, ...}
csdb.read_dictionary("Assignments.csdb")  # the .dcf that made it
rows = csdb.read_cases("Assignments.csdb")# flat rows, ready for add_cases()

csdb.import_csdb(csync.session(), "Assignments.csdb")   # straight into the local store
csdb.create("out.csdb", dictionary, rows)               # write a fresh file
csdb.append_cases("Assignments.csdb", rows)             # add to a CSPro-made file
```

Reading is exact (tested against real CSPro 8 files, both the expanded-table and
JSON-questionnaire layouts). For writing, `append_cases` on a CSPro-made file is the safe
direction; `create` builds a file from scratch for demos, tests and seeding — CSPro
recomputes its own `dictionary_structure` signature when it opens such a file, so for
production data a file produced by CSPro, CSExport or Excel-to-CSPro (`xl2cs`) stays
authoritative.

## The example: an assignments module

`csync/modules/assignments.py` shows the intended pattern — **the generic core stays
dictionary-agnostic; a small module gives one dictionary's items their meaning.**

```python
from csync.modules.assignments import Assignments

a = Assignments()
a.assign((101, 7), "UG-1001", staff_name="Aidah Nakato", role="INTERVIEWER", workload=120)
a.assign_many([{"area": (101, 8), "staff_code": "UG-1001", "workload": 90}])
a.reassign((101, 8), "UG-1002", staff_name="Brian Okello")
a.unassign((101, 9))

a.summary()              # {'assignments': 14, 'staff': 6, 'by_status': {...}, 'dirty': 3}
a.workload_by_staff()    # Counter({'UG-1001': 2, ...})
a.for_staff("UG-1001")

a.refresh()              # pull field progress
a.publish()              # push the new assignments; tablets pick them up on their next sync
```

Try it end to end with no server at all:

```bash
python examples/make_example_csdb.py      # build examples/sample_assignments.csdb
python examples/assignments_demo.py       # import it, add, reassign, remove, summarise
python examples/assignments_demo.py --sync   # ... and sync, once .env is filled in
```

The repo ships `examples/SAMPLE_ASSIGNMENTS.dcf`, the generated `sample_assignments.csdb`
and a CSV to import.

## Dictionary-agnostic: what is, what isn't

**Agnostic — no per-survey code, ever.** Everything in `csync/dictionary.py`,
`client.py`, `store.py`, `session.py`, `csdb.py`, `cli.py` and `web.py`: parsing either
`.dcf` format, building and parsing case keys, the `level-1` wire format, add/update/remove,
`sync_up` / `sync_down`, dirty tracking, vector clocks, tombstones, reading and writing
`.csdb`, the CSV importer, and the web form (generated from the dictionary at runtime).

**Not agnostic — three things, by nature:**

| What | Why | Where |
|---|---|---|
| Domain meaning ("who is assigned where", "has fieldwork started") | Item *names* carry meaning only for a human | `csync/modules/assignments.py` |
| Universes | A universe is a key-prefix expression built from *your* id items | the `universe=` argument you pass |
| Multi-level dictionaries | Only level 1 is synced (see Limitations) | `Dictionary.levels` tells you |

### Using a different dictionary

For the **generic** functions: nothing to do. Point them at the name —
`csync.sync_down("OTHER_DICT")` — and the `.dcf` does the rest (from `CSYNC_DICTS`, the
store cache, or the server).

For a **domain module** like assignments, copy the `Fields` block and edit it:

```python
from csync.modules.assignments import Assignments, Fields

a = Assignments(fields=Fields(
    dictionary="NPHC2024_ASSIGNMENTS",
    area=("AA_REGION", "AA_DISTRICT", "AA_EA"),   # the id items that identify the place
    staff_code="AA_STAFF_CODE",
    staff_name=None,                              # this dictionary has no name item
    role="AA_ROLE",
    status=None,
    workload=None,
    assigned_on="AA_DATE_ASSIGNED"))
```

1. Put the `.dcf` in `CSYNC_DICTS` (or let it be pulled from the server).
2. Name the dictionary and its id items in `area`.
3. Map each meaning to an item name; set anything the dictionary lacks to `None` — those
   fields are simply not written.
4. Everything else (assign, reassign, unassign, workload, publish) works unchanged.

## Limitations

- **Level 1 only.** CSPro allows up to three levels; the sync wire format here handles the
  first. `Dictionary.levels` tells you if a dictionary has more.
- **Item occurrences** (an item repeated *within* a record) are not expanded; repeating
  *records* are.
- **Subitems** are skipped deliberately — they overlap their parent item's columns.
- **`create()` for `.csdb`** writes its own structure signature (see above).
- **Notes and binary/media items** are not synced.

## Tests

```bash
python -m pytest -q        # 24 tests, no server needed
```

The suite runs against a fake CSWeb that keeps the cases exactly as they arrive, so the
tests also pin the wire format down: a single-occurrence record must be an object, the key
must be zero-filled, the clock must be bumped, a delete must go up as a tombstone.

## Repo layout

```
csync/
  __init__.py        module-level shortcuts (csync.add_case, csync.sync_up, ...)
  config.py          Settings + the .env reader
  dictionary.py      .dcf parser (JSON and INI) + the case wire format
  client.py          the CSWeb REST client (standard library only)
  store.py           the local SQLite mirror
  session.py         the case functions and sync_down / sync_up
  csdb.py            real CSPro .csdb files: read, append, create
  cli.py             python -m csync ...
  web.py             the optional Flask page
  templates/index.html
  modules/assignments.py    the example domain module
docs/                swagger spec + viewer + the short API notes
examples/            sample .dcf, generated .csdb, CSV, runnable demos
tests/               pytest suite with a fake CSWeb server
```
