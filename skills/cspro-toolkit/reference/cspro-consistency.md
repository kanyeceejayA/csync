# Cross-file consistency in a CSPro app

A CSPro application is several files that must agree. `cspro_lint.py` (directory
mode) enforces the links below so that a change in one file cannot silently break
another. This is the "keep the dictionary, form, and logic in sync" guarantee.

## The dependency graph

```
.ent  (JSON manifest)  ── lists ──►  .dcf (dicts) · .fmf (forms) · .apc (logic) · .qsf · .mgf
.fmf  [Dictionaries] File= ─────────►  .dcf
.fmf  [Field] Item=X,DICT ──────────►  item X must exist in dictionary DICT
.fmf  [Form]  Item=X ───────────────►  X is a .dcf item or a roster form (<RECORD>000)
.apc  PROC <name> ──────────────────►  a .dcf item / .fmf field / form / level / group / GLOBAL
.apc  UPPER_SNAKE refs ─────────────►  a .dcf/.fmf symbol, a declared local, or a keyword
.pff  [ExternalFiles] NAME=path ────►  NAME = a .dcf top-level `name`; path (.csdb) must exist
```

## What breaks when you edit each file — and which check catches it

| You change… | Risk | Check (severity) |
|-------------|------|------------------|
| Rename/delete a **dict item** in `.dcf` | Form field bound to old name; logic refs old name | `.fmf` field→dict (ERROR); PROC orphan + logic ref (WARN) |
| Rename a **dict top-level name** | `.pff` `[ExternalFiles]` / `.fmf` `Item=…,DICT` point at old name | pff-name (WARN); fmf field-dict (ERROR) |
| Delete/rename a **form field** in `.fmf` | Its `PROC` in logic no longer fires | PROC orphan (WARN) |
| Move/rename a referenced file (`.qsf`, `.mgf`, `.apc`, `.fmf`, `.dcf`) | App won't load | `.ent` manifest (ERROR) |
| Add a `PROC` for a field not on any form | Dead proc (never fires) | PROC orphan (WARN) |
| Point `.pff` external at a missing `.csdb` | Runtime failure | pff data-file (WARN) |

## Key facts (CSPro 8.x)

- **`.dcf`** = source of truth for item/record/level/valueset **names**, positions,
  lengths, value sets. JSON in 8.x; INI text (`Name=`) in ≤7.x. The linter reads both.
- **`.fmf`** = INI text. `[Field]` blocks carry `Item=<item>,<dictname>` — the binding
  that breaks on a dict rename. Rosters appear as `Item=<RECORD>000` in `[Form]` blocks
  and as `Name=<RECORD>000` form entries.
- **`.ent`** = JSON manifest: `dictionaries[].path`, `forms[]`, `code[].path`,
  `questionText[]`, `messages[]`. All paths are relative to the `.ent`.
- **`.pff`** = INI run file. `[ExternalFiles] NAME=path.csdb` where `NAME` must equal a
  dictionary's top-level `name`; the value is the **data** file, not the `.dcf`.
- A single field name legitimately lives in the dict AND as a form field AND as a
  `PROC` target — that is normal, not duplication.

## Editing discipline

When you rename or delete a dictionary item, update all three in the same change:
1. the `.dcf` item,
2. every `.fmf` `[Field] Item=` (and `[Form] Item=`) that binds it,
3. every `PROC <item>` and logic reference in the `.apc`,
4. any `.pff` `[ExternalFiles]` name if you renamed a dictionary.
Then run `cspro_lint.py <project>` — it should return to 0 errors.
