# Claude Code skills

`cspro-toolkit/` is the Claude Code skill used alongside `csync` to work on CSPro
applications: reading and editing dictionaries, logic, forms and `.pff` files; a static
linter (`scripts/cspro_lint.py`) for logic and cross-file checks, since CSPro cannot
compile an entry application headlessly; building and deploying packages with CSDeploy;
and inspecting a CSWeb server (`scripts/csweb.py`, both the 8.0 and 8.1 API specs in
`reference/`).

A skill only works on a machine where Claude Code can find it. Install it into your
user skills folder:

```bash
# macOS / Linux / Git Bash
mkdir -p ~/.claude/skills && cp -r skills/cspro-toolkit ~/.claude/skills/
```

```powershell
# Windows PowerShell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills" | Out-Null
Copy-Item -Recurse -Force skills\cspro-toolkit "$env:USERPROFILE\.claude\skills\"
```

Then start a new Claude Code session; the skill is picked up automatically when you work
on CSPro files, or can be called by name.

**Keeping one copy.** This folder is the copy to edit and commit. To have Claude Code use
it directly instead of a separate copy, replace the installed folder with a link to this
one (Windows: `mklink /J "%USERPROFILE%\.claude\skills\cspro-toolkit" "<repo>\skills\cspro-toolkit"`
from `cmd`, after removing the old folder; macOS/Linux: `ln -s`).

Nothing in the skill holds credentials: `csweb.py` reads `CSWEB_URL`, `CSWEB_USER` and
`CSWEB_PASSWORD` from the environment.
