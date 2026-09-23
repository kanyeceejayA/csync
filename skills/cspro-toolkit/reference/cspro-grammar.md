# CSPro logic block grammar (as encoded by cspro_lint.py)

Learned empirically from real CSPro 8.x apps and verified to balance to zero on
known-good code. CSPro's grammar has several traps that naive keyword-counting
gets wrong — these are the rules that matter.

## Block openers → closers

| Opener | Closer | Notes |
|--------|--------|-------|
| `if … then` | `endif` | `elseif`/`else` are middles, not new blocks. |
| `do varying …` / `do numeric … while/until …` | `enddo` | do-loop. The `while`/`until` *inside* it is a CLAUSE, not an opener. |
| `while <cond> do` | `enddo` | while-loop. The trailing `do` is a clause keyword. |
| `for <var> in <rec> [do]` | `endfor` or `enddo` | the `do` is optional. |
| `forcase <dict>(…) do` | `enddo` or `endfor` | do-loop style; `do` is a clause. |
| `function name(…)` | `end` | bare `end` (distinct token from endif/enddo). |
| `recode` | `endrecode` | rare. |

## Gotchas the linter handles

1. **`enddo` and `endfor` are INTERCHANGEABLE** loop terminators. CSPro accepts
   `forcase … do … endfor` *and* `forcase … do … enddo`; same for `for`/`while`/`do`.
   The linter lets either closer match any loop opener.

2. **`forcase … do` uses `do` as a clause, not an opener.** The clause `do` is
   detected because a `for`/`forcase`/`while` header is "awaiting its do". The body
   right after it often starts with a `numeric`/`string` *declaration* — do NOT
   treat `do numeric` there as a do-loop. (A real do-loop is `do varying …` or a
   statement-initial `do numeric … while`.)

3. **`ask if <cond>;` is a STATEMENT, not a block.** A *block* `if` always has a
   `then` before its `;`. The linter classifies an `if` as a block only if `then`
   appears before the next `;`.

4. **`endgroup;` in entry/batch logic is a flow STATEMENT** (end the current group
   occurrence), NOT a block closer. group/endgroup *blocks* live only in `.fmf`
   form files. The linter does not treat `endgroup` structurally.

5. **`select(…)` is a function**, not a block. There is no `select`/`endselect`.

6. **Comments and strings must be stripped first.** CSPro comments: `//` line,
   `/* … */` block, and `{ … }` brace comments. Strings: `"…"` and `'…'`. Raw
   keyword counts are meaningless (e.g. "if"/"endif" won't match because "if"
   appears constantly in comments). The linter blanks all of these (preserving
   newlines) before parsing.

## Symbol namespaces (for the dict↔logic sync check)

A name used in logic is valid if it is one of:
- a **dictionary** symbol — any `name` in a `.dcf` (level, record, item, valueset);
- a **form/roster** name — any `Name=` in a `.fmf` (rosters are `<RECORD>000`);
- a **declared** local — `numeric x`, `string s`, `list string L`, `array`,
  `function f(...)` name + its params, `do varying <var>`;
- a **reserved word** — see `keywords.txt`.

The sync check only flags UPPER_SNAKE tokens (the dict-reference convention) to
keep noise near zero; it will not catch a mistyped lower-case local (only the
real compiler will).
