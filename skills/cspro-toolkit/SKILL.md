---
name: cspro-toolkit
description: >-
  Understand, edit, and pre-check CSPro (Census and Survey Processing System)
  applications — the .dcf dictionaries, .apc/.app logic, .pff run files, .fmf
  forms, .ent entry apps — and query a CSWeb sync server's REST API (list
  dictionaries & case counts, download dicts, read cases, sync history, apps,
  files, users). Use whenever working in a CSPro project: reading or modifying
  survey logic, keeping dictionaries and logic in sync, syntax-checking a logic
  change, building or releasing an app package (.csds / CSDeploy), or inspecting
  what a CSWeb server holds. Triggers on: CSPro, CSEntry, .apc, .dcf, .pff, .csds,
  "data entry application", dictionary/logic sync, forcase, "syntax check my
  CSPro", deploy, CSDeploy, "deploy the app", package.json signature, CSWeb,
  csweb, sync server, "cases on the server", sync history.
---

# CSPro Toolkit

Helps read and safely modify CSPro 8.x applications, and pre-checks logic edits
with a static linter (`scripts/cspro_lint.py`) since CSPro **cannot compile an
entry application headlessly** (see "The compile limitation" below).

## CSPro project anatomy

A CSPro app is a set of sibling files, usually one folder per application:

| Ext      | What it is | Format |
|----------|-----------|--------|
| `.dcf`   | **Data dictionary** — levels → records → items → value sets. The single source of truth for field names, positions, lengths, valid values. | CSPro 8.x = **JSON**; CSPro ≤7.x = INI text (`[Dictionary]`, `Name=`) |
| `.apc` / `.app` | **Logic** (CSPro's proprietary language). `PROC GLOBAL` + one `PROC <field>` per field with `preproc`/`postproc`/`onfocus` events. | UTF-8 text (often BOM) |
| `.fmf`   | **Form file** — screens, rosters, field capture order. Defines *form* and *roster* names (e.g. `LISTING_DU_REC1000` = roster form for record `LISTING_DU_REC1`). | INI text (`Name=`) |
| `.ent`   | Entry application (binds dict + form + logic). | INI text |
| `.qsf`   | Question text / CAPI question spec. | JSON-ish |
| `.mgf`   | Message file (numbered user messages). | text |
| `.pff`   | **Program Information File** — how to *run* an app: which `.ent`, input `.csdb`, and `[ExternalFiles]` dictionary→data mappings. | INI text |
| `.csds`  | **Deployment specification** — which files/dictionaries make up a releasable package, and where it is sent. Built and shipped by `CSDeploy.exe` via an `AppType=Deploy` pff; see "Deploying" below. | CSPro 8.x = **JSON** |
| `.csdb`  | Data (SQLite). `.sva` = save array. `.cslog` = paradata. | binary/SQLite |

**Dictionary ↔ logic ↔ pff must stay in sync:**
- Every dictionary item/record/valueset **name** in the `.dcf` is a symbol usable in logic.
- Logic references those names directly (e.g. `LI_REGION`, `count(LISTING_DU_REC1)`).
- Rosters are referenced by their **form** name (dict record `FOO` → form `FOO000`).
- Each dictionary the logic uses must be declared in the `.pff` `[ExternalFiles]`
  (name = dictionary's top-level `name`, value = path to its `.csdb`).
- Rename an item in the `.dcf` → every logic reference and pff mapping must change too.

## Editing workflow (do this every time)

1. **Read before editing.** Parse the relevant `.dcf` to know exact item names,
   lengths, positions, and value sets. Never guess a field name — grep the `.dcf`.
2. **Match surrounding style** (this codebase: tabs, `//` comments, camelCase
   locals, UPPER_SNAKE dict items with a project prefix like `LI_`, `I_`, `GEO_`).
3. **Preserve the BOM and encoding.** These files are UTF-8; many have a BOM.
   When editing programmatically, read/write with `utf-8-sig`.
4. **Pre-check with the linter** (below) before handing back.
5. **Authoritative check = CSPro Designer.** Tell the user to open the app in
   CSPro and compile (see limitation). The linter is a safety net, not a compiler.

**Save before linting.** The linter reads the file **on disk**; the CSPro editor
compiles its **in-memory buffer**. If the user has unsaved edits open in CSPro, the
linter and the compiler will disagree — have them save first. (Structural errors
like a missing `endif` cascade in the real compiler into a flood of
"not a declared variable" / "PostProc clause expected" errors; fix the *first*
structural error and most of the rest vanish.)

## Syntax + consistency pre-check — `scripts/cspro_lint.py`

Static analyzer. **No CSPro engine required.** Catches the highest-frequency
edit mistakes AND cross-file (`.dcf`/`.fmf`/`.ent`/`.pff`) desyncs, with zero
false positives on well-formed code.

```bash
python scripts/cspro_lint.py <project_dir>              # whole project (all checks)
python scripts/cspro_lint.py <file.apc>                 # one logic file (auto-finds sibling dicts/forms)
python scripts/cspro_lint.py <file.apc> --dicts <dir>   # point at dictionaries explicitly
python scripts/cspro_lint.py <project_dir> --balance-only     # only block/bracket balance
python scripts/cspro_lint.py <project_dir> --no-consistency   # skip cross-file checks
python scripts/cspro_lint.py <file.apc> --strict        # also flag every unknown identifier (noisier)
```

Exit code `1` if any ERROR, else `0`. **Run it after every logic OR dictionary
OR form edit** — it is the fast way to confirm a change in one file didn't break another.

**Per-file syntax**
- **Block balance** — `if/endif`, `do…enddo`, `while…enddo`, `for/forcase…endfor`,
  `function…end`. Models CSPro's real (quirky) grammar — see `reference/cspro-grammar.md`.
  Catches a deleted/extra `endif`, mismatched loop terminator, unclosed function.
- **Bracket balance** — `()` and `[]`.

**Cross-file compatibility (the "change one file, break another" guard)** — see
`reference/cspro-consistency.md`:
- **`.dcf` → `.fmf`** (ERROR): every form `[Field] Item=X,DICT` must exist in dict
  `DICT`. Rename/delete a dictionary item and forget the form → caught at the form line.
- **`.ent` manifest** (ERROR): every dict/form/code/qsf/mgf the app references must exist.
- **`.fmf` → `.dcf`** (ERROR): the form's `[Dictionaries] File=` must exist.
- **`.apc` PROC targets** (WARN): every `PROC <name>` must resolve to a real dict item,
  field, form, level, or group (`GLOBAL` excepted). Orphan PROC = dict/logic drift.
- **`.dcf` ↔ `.apc` sync** (WARN): UPPER_SNAKE tokens in logic that exist in no `.dcf`/`.fmf`.
- **`.pff` → `.dcf` names** (WARN): every `[ExternalFiles]` key must be the top-level
  `name` of a project `.dcf` (catches naming drift like `UNHS2024_*` vs `UNHS2026_*`);
  its data file must exist.

**What it CANNOT catch** (only the real compiler can): type mismatches, wrong
function arguments/arity, undefined *lower-case* locals in default mode, bad
statement syntax that is still bracket-balanced. So: **linter clean ≠ compiles.**
`--strict` extends the identifier check to lower-case names but also flags CSPro
enum/property constants (e.g. `latitude`, `baseMap`), so use it as a targeted
typo hunt, not a clean-run gate.

## The compile limitation (important — verified empirically)

There is **no reliable fully-headless compile for a CSPro *entry* application.**
`CSProProductionRunner.exe`/`CSEntry.exe` open a GUI, and a compile error surfaces
as a **modal dialog**; the error is not flushed to a listing file you can read when
you kill the process (tested with `ErrorList=`/`Listing=` PFF attributes — nothing
written). The CLR wrappers (`zLogicCLR.dll`) expose a logic *colorizer* and a
dictionary object model, but **no headless logic compiler**.

Therefore the authoritative syntax check is a human action:
**open the app in CSPro Designer (`CSPro.exe`) and Compile (writes `<app>.ent.err`
"CSPro Error Summary").**

Batch compile-check was also tested and does **not** give a hand-drivable headless
path: `CSBatch.exe` is the batch *designer* GUI (window "CSPro Batch Edit"), not a
runner; `CSProProductionRunner.exe <batch.pff>` shows a persistent "CSPro Production
Runner" window and writes no listing when killed; `runwait.exe` returns but produces
no output for a hand-authored `.bch`. Authoring a valid batch harness requires the
designer. Net: rely on the static linter for headless pre-checks, the Designer for
the authoritative compile.

**But a deploy build *does* compile, headlessly.** `CSDeploy.exe <deploy.pff>` with
`DeployToOverride=LocalFolder` turns every `.ent` listed in the `.csds` into a fresh
`.pen` in the output folder, with no GUI (see "Deploying" below). So when a project
has a `.csds`, a local deploy build is a genuine headless build path — and the way to
get an updated `.pen` without asking the user to open Designer.

Two caveats before treating it as a compile *check*: it exits as soon as the build /
deploy is triggered, so its **exit code is not a compile verdict**; and how a logic
error surfaces there is untested. Verify the build instead of trusting the exit code:
confirm the output `.pen` timestamp moved and that it contains a string you just
added (a `.pen` is bzip2-compressed UTF-16LE — decompress and search it).

## Deploying an application — `.csds` + its `AppType=Deploy` pff

A `.csds` is the **release manifest** for an app: what goes in the package and where
the package is sent. It is JSON with `"fileType": "deployment"`:

```json
{
  "software": "CSPro", "version": 8.0, "fileType": "deployment",
  "name": "MYAPP",
  "description": "...",
  "files":        [ { "path": "..\\Menu\\Menu.ent" }, { "path": "..\\Data\\Ref.csdb" } ],
  "dictionaries": [ { "path": "..\\Dictionaries\\STAFF.dcf", "uploadForSync": true } ],
  "deployment": { "type": "CSWeb", "cswebUrl": "http://host/csweb8/api",
                  "ftpUrl": "ftp://", "localFolderPath": "..\\My Deploy" }
}
```

- `name` is the package name; on a CSWeb target the tree lands under `apps/<name>/`.
- `files[]` paths are relative to the `.csds`. **They list the source `.ent`, not the
  compiled `.pen`** — building the package compiles each entry app and writes the
  `.pen` into the output. *Deploying is therefore also the build step* (see the
  compile note above).
- `dictionaries[].uploadForSync: true` registers/updates that dictionary's **schema**
  on the CSWeb server, independently of any case data.
- `deployment.type` is `LocalFolder` | `CSWeb` | `FTP`.

Its pff is tiny — `AppType=Deploy`, pointing at the `.csds`:

```ini
[Run Information]
Version=CSPro 8.0
AppType=Deploy

[Files]
Application=.\My App.csds

[Parameters]
DeployToOverride=LocalFolder      ; optional: overrides deployment.type
```

Run it with **`CSDeploy.exe "path\to\deploy.pff"`**. Projects commonly keep two
pffs beside the `.csds`: the real one, and a `DeployToOverride=LocalFolder` variant
that builds into a local folder without touching any server — that local build is
the safest way to compile the apps and inspect the result before releasing.

### Testing a build during development: push to the tablet, NOT to the server

Publishing to CSWeb is a *release* action — it hands the build to every field
device that runs "Update the application". **During development, don't do it.**
Build locally and push straight to the tablet over adb; publish only when the
build is meant for the field.

```bash
# 1. build locally (this is also the headless compile - check for a .err first)
CSDeploy.exe "…\Deploy<App>LocalFolder.pff"      # DeployToOverride=LocalFolder

# 2. push just what changed, into the app's own folder
APP=/storage/emulated/0/Android/data/gov.census.cspro.csentry/files/csentry/<APP>
adb push "Deploy\<APP>\Menu\Menu.pen"            "$APP/Menu/Menu.pen"
adb push "Deploy\<APP>\<Questionnaire>.pen"      "$APP/<Questionnaire>/…"
adb push "Deploy\<APP>\package.json"             "$APP/package.json"
adb push "Deploy\<APP>\package.json"             "$APP/package.csds"   # same document

# 3. ALWAYS fix the mode afterwards - see below
adb shell chmod 660 "$APP/Menu/Menu.pen" "$APP/<Questionnaire>/…pen"
```

**The adb-push trap (this WILL break in-app updates if you skip step 3).**
`adb push` writes files owned by `shell` with mode `644`; files CSEntry creates
are owned by the app user (`u0_aNNN`) with mode `660`. CSEntry only has *group*
rights on a shell-owned 644 file, and group has no write bit — so the next
`syncapp()` cannot overwrite the pushed `.pen` and fails with **"error
extracting application"**. The package is fine; the file mode is not. Confirm
with `adb shell ls -la "$APP/Menu"` — a `shell … -rw-r--r--` line next to
`u0_aNNN … -rw-rw----` lines is the tell.

Recovery on an already-broken tablet: `adb shell chmod 660 <the pushed files>`
(adb owns them, so chmod is allowed), or delete the app folder and reinstall
from the server, which restores correct ownership throughout.

### The built package

The output tree gains **`package.json` and `package.csds` — byte-identical documents**.
Each `files[]` entry gains a `signature` = **uppercase hex MD5** of the *built* file,
plus a top-level `buildTime`. On a CSWeb target the same tree lands under
`apps/<name>/` in the file store with both manifests at its root.

**CSEntry decides what to re-download by comparing those signatures**, so a file
replaced in the file store without refreshing **both** manifests is invisible to
devices — the tablet keeps running the old build. If you ever hand-push a file
(`PUT /api/files/{path}/content`, hex `Content-MD5`), rewrite `package.json` *and*
`package.csds` in the same pass.

### `CSDeploy.exe` returns before the deployment finishes

It exits as soon as the deploy has been **triggered**; the transfer proceeds after
the process is gone. **Exit code 0 means "started", not "finished"** — checking the
server immediately after will still show the previous build and invite the wrong
conclusion that nothing happened. Poll the target instead (for CSWeb: re-read
`apps/<name>/package.json` until `buildTime`/signatures change, or `file-info` the
one file you expect to move).

Do **not** conclude from an immediate check that the deploy failed and start
hand-pushing files — that is a real trap: the upload was simply still in flight, and
a manual push on top of it duplicates work and risks racing the tool.

## CSWeb sync server (REST API)

CSEntry devices sync data and app packages to a **CSWeb** server. To inspect
what's on a server — which dictionaries exist and their **case counts**,
individual cases, **sync history** (who synced what, when), deployed app
packages, server files/folders, and users — use the sync REST API.

- **Reference:** `reference/csweb-sync-api.md` (auth flow, every endpoint,
  paging/delta headers, CLI examples).
- **Full spec:** `reference/csweb-swagger.json` (OpenAPI 2.0, "CSPro Sync" v2.0 =
  CSWeb 8.0) and `reference/csweb81-swagger.json` (CSWeb 8.1.2, REST API 3.0 - the
  file is still labelled 2.0.0 and omits the 8.1-only routes; the reference's
  "CSWeb 8.1" section lists what it leaves out). `GET /server` -> `apiVersion`
  tells them apart: 2.0 = CSWeb 8.0, 3.0 = CSWeb 8.1.
- **Helper CLI:** `scripts/csweb.py` — stdlib-only (no pip installs), one
  subcommand per endpoint.

**Deploying:** `.pen` is a compiled binary produced only by the CSPro Deploy tool —
source edits never change it. CSDeploy uploads a **zip package**, which must then be
**unzipped** into `/apps/{NAME}/…`; devices pull individual files from that unzipped
tree, not from the package. When a deploy "didn't reach the tablets", check the
package `buildTime` against the unzipped file's `lastModified` before anything else —
see "Deploying an app, and the stale-unzip trap" in the reference.

```bash
export CSWEB_URL=https://csweb.example.org CSWEB_USER=admin CSWEB_PASSWORD=secret
python scripts/csweb.py dicts                    # dictionaries + caseCount
python scripts/csweb.py dict-get UNHS2026 --out server.dcf   # download the .dcf
python scripts/csweb.py cases UNHS2026 --count 50            # cases (paged; etag on stderr)
python scripts/csweb.py syncs UNHS2026 --limit 100          # sync history
python scripts/csweb.py apps                                 # deployed app packages
```

Key facts (details in the reference):
- **Everything is under `{server}/api`** and needs a token. Auth is OAuth2
  **password grant** at `POST /api/token` with the fixed CSWeb client
  (`client_id=cspro_android`, `client_secret=cspro`) plus a real user's
  `username`/`password`; then `Authorization: Bearer <token>` on every call.
  Only `/token` is unauthenticated (**`/server` also needs the token**).
- **`POST /dictionaries/` needs its trailing slash.** CSWeb is a Symfony app and
  that route is slash-terminated; Symfony only auto-redirects a slash mismatch for
  safe methods, so `GET /dictionaries` works while `POST /dictionaries` returns a
  **404 HTML page** and `POST /dictionaries/` returns `200 Success`. An HTML error
  body instead of the JSON `{"code","description"}` envelope is the tell. This is
  the only API route back after `dict-delete`, so do not let it look unimplemented.
- **Cases page via headers**, not query params: `x-csw-case-range-count` (limit),
  `x-csw-case-range-start-after` (GUID cursor), and an `etag` → `If-Match` for
  fetching only new/changed cases (`412` = etag no longer known, re-pull fresh).
- **This closes the loop with the linter:** a `.dcf` pulled with `dict-get` is the
  same dictionary you lint locally — diff it against the project `.dcf` to catch
  server-vs-local drift, or run `cspro_lint.py` on it.
- **Live data.** The CLI is read-only by design (plus `dict-add`); `dict-delete`
  wipes a dict's cases, and case/file/app writes are intentionally not wired in.
  Keep credentials in the `CSWEB_*` env vars, not in argv.

## Safe testing

Copy the project folder elsewhere and run the linter / experiment there — never
mutate the live project while the user may have it open in CSEntry/CSPro. Check
for running `CSPro.exe`/`CSEntry.exe` before launching any CSPro tool.

## References
- `reference/keywords.txt` — full CSPro 8.0 reserved-word list (from the installed
  `userDefineLang.xml`), used by the linter's sync check.
- `reference/cspro-grammar.md` — the block-grammar rules the linter encodes, with
  the gotchas (`forcase…do…enddo`, interchangeable `enddo`/`endfor`, `ask if`,
  `endgroup;` as a statement).
- `reference/cspro-consistency.md` — the cross-file (`.dcf`/`.fmf`/`.ent`/`.pff`)
  consistency rules the linter enforces.
- `reference/csweb-sync-api.md` + `reference/csweb-swagger.json` — the CSWeb sync
  server REST API (auth, endpoints, paging); driven by `scripts/csweb.py`.
- Deployment (`.csds`, `AppType=Deploy` pff, `CSDeploy.exe`, package signatures)
  is covered inline above under "Deploying an application".
- Official docs: https://www.csprousers.org/help/ and forum https://www.csprousers.org/forum/
