#!/usr/bin/env python3
"""
cspro_lint.py - static analysis / syntax pre-check for CSPro 8.x applications.

CSPro cannot compile an *entry* application headlessly (compile errors surface as
a modal GUI dialog in CSEntry / CSProProductionRunner). This linter catches the
highest-frequency mistakes WITHOUT the CSPro engine, so an editor (human or AI)
can gain confidence before doing the authoritative compile in the CSPro Designer.

It is a HEURISTIC pre-check, not a full compiler. It reports:
  1. Block-balance errors  (if/endif, do/enddo, for/endfor, function/end, group/endgroup, ...)
  2. Bracket/paren balance  ( ) [ ]
  3. Dictionary<->logic sync (identifiers that look like dict refs but exist in no .dcf)
  4. PFF<->dictionary sync   (external dicts in .pff missing a .dcf / .csdb)

Usage:
  python cspro_lint.py <project_dir>                 # lint whole project
  python cspro_lint.py <file.apc> [--dicts DIR ...]  # lint one logic file
  python cspro_lint.py <project_dir> --balance-only  # skip the noisy sync check
"""
import sys, os, re, json, glob, argparse
from collections import defaultdict

# ---------------------------------------------------------------- comment/string strip
def strip_noise(src):
    """Remove // line comments, {…} and /*…*/ block comments, and "…" strings.
    Replaces each removed span with spaces so column/line numbers are preserved."""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        two = src[i:i+2]
        if two == '//':
            j = src.find('\n', i)
            j = n if j == -1 else j
            out.append(' ' * (j - i)); i = j
        elif two == '/*':
            j = src.find('*/', i+2)
            j = n if j == -1 else j+2
            out.append(''.join(ch if ch=='\n' else ' ' for ch in src[i:j])); i = j
        elif c == '{':                      # CSPro brace comment
            j = src.find('}', i+1)
            j = n if j == -1 else j+1
            out.append(''.join(ch if ch=='\n' else ' ' for ch in src[i:j])); i = j
        elif c == '"':
            j = i+1
            while j < n and src[j] != '"':
                if src[j] == '\n': break
                j += 1
            j = min(j+1, n)
            out.append(''.join(ch if ch=='\n' else ' ' for ch in src[i:j])); i = j
        elif c == "'":
            j = i+1
            while j < n and src[j] != "'":
                if src[j] == '\n': break
                j += 1
            j = min(j+1, n)
            out.append(''.join(ch if ch=='\n' else ' ' for ch in src[i:j])); i = j
        else:
            out.append(c); i += 1
    return ''.join(out)

# ---------------------------------------------------------------- block balance
# CSPro block grammar (learned from real 8.x apps):
#   if ...                       endif
#   do varying/numeric/... while/until ... enddo    (do-loop; the 'while'/'until'
#                                                     inside it is a CLAUSE, not an opener)
#   while <cond> do ...          enddo              (while-loop: 'while' opens, 'do' is a clause)
#   for <var> in <rec> [do] ...  endfor
#   forcase <dict>(...) do ...   endfor
#   group ...                    endgroup
#   recode ...                   endrecode
#   function name(...) ...       end                (bare 'end')
_LOOP = {'do', 'while', 'for', 'forcase'}
# CSPro is lenient: enddo and endfor are interchangeable terminators for ANY loop
# construct (do / while / for / forcase).
# NOTE: 'endgroup' is intentionally NOT a structural closer. In entry/batch logic
# 'endgroup;' is a flow-control STATEMENT (end the current group occurrence); the
# group/endgroup *block* only exists in form files, not in logic.
CLOSER_TO_OPENERS = {
    'endif':     {'if'},
    'enddo':     set(_LOOP),
    'endfor':    set(_LOOP),
    'endrecode': {'recode'},
    'end':       {'function'},
}
EXPECTED_CLOSER = {
    'if': 'endif', 'do': 'enddo', 'while': 'enddo', 'for': 'endfor',
    'forcase': 'enddo', 'recode': 'endrecode', 'function': 'end',
}
CLOSERS = set(CLOSER_TO_OPENERS)
# tokens after which a 'while' begins a NEW statement (i.e. is a loop opener, not a do-clause)
WHILE_OPENER_PREV = {None, ';', 'then', 'else'} | CLOSERS

TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|;")

def check_balance(clean, path, issues):
    toks = [(m.group(0).lower(), m.group(0), clean.count('\n', 0, m.start()) + 1)
            for m in TOKEN.finditer(clean)]
    words = [t[0] for t in toks]
    stack = []                     # (opener_keyword, line)
    prev = None                    # previous significant token (identifiers and ';')
    awaiting_do = False            # a for/forcase/while header is awaiting its clause 'do'
    for idx, (low, orig, ln) in enumerate(toks):
        nxt = words[idx+1] if idx+1 < len(words) else None
        is_opener = None
        if low == ';':
            awaiting_do = False
        elif low == 'if':
            # block 'if' always has a 'then' before its statement terminator ';'.
            # 'ask if cond;' (and other statement-'if' forms) have no 'then'.
            for j in range(idx+1, len(words)):
                if words[j] == 'then':
                    is_opener = 'if'; break
                if words[j] == ';':
                    break
        elif low in ('for', 'forcase', 'recode', 'function'):
            is_opener = low
        elif low == 'do':
            if awaiting_do:
                awaiting_do = False           # this is the clause 'do' of for/forcase/while
            elif nxt in ('varying', 'numeric', 'string') or prev in WHILE_OPENER_PREV:
                is_opener = 'do'
        elif low == 'while':
            if prev in WHILE_OPENER_PREV:
                is_opener = 'while'
            # else it's the 'while' clause of a do-loop -> ignore

        if is_opener:
            stack.append((is_opener, ln))
            if is_opener in ('for', 'forcase', 'while'):
                awaiting_do = True
        elif low in CLOSERS:
            valid = CLOSER_TO_OPENERS[low]
            if not stack:
                issues.append((path, ln, 'ERROR',
                    f"'{orig}' with no matching {'/'.join(sorted(valid))}"))
            elif stack[-1][0] not in valid:
                issues.append((path, ln, 'ERROR',
                    f"'{orig}' but innermost open block is '{stack[-1][0]}' "
                    f"(opened line {stack[-1][1]}, expected '{EXPECTED_CLOSER[stack[-1][0]]}')"))
                stack.pop()
            else:
                stack.pop()
        if low != ';' or True:
            prev = low
    for tok, ln in stack:
        issues.append((path, ln, 'ERROR',
            f"'{tok}' block opened here is never closed (expected '{EXPECTED_CLOSER[tok]}')"))

def check_brackets(clean, path, issues):
    pairs = {')': '(', ']': '['}
    opens = set(pairs.values())
    stack = []
    for m in re.finditer(r"[()\[\]]", clean):
        ch = m.group(0); ln = clean.count('\n', 0, m.start()) + 1
        if ch in opens:
            stack.append((ch, ln))
        else:
            if not stack or stack[-1][0] != pairs[ch]:
                issues.append((path, ln, 'ERROR', f"unbalanced '{ch}'"))
            else:
                stack.pop()
    for ch, ln in stack:
        issues.append((path, ln, 'ERROR', f"'{ch}' never closed"))

# ---------------------------------------------------------------- dictionary symbols
def load_dict_symbols(dcf_path):
    names = set()
    raw = open(dcf_path, encoding='utf-8-sig', errors='replace').read()
    if not raw.lstrip().startswith('{'):
        # Older INI-style text dictionary: names live on 'Name=...' lines.
        for m in re.finditer(r'^\s*Name\s*=\s*(\S+)', raw, re.M):
            names.add(m.group(1).upper())
        return names, None
    try:
        j = json.loads(raw)
    except Exception as e:
        return names, str(e)
    def walk(o):
        if isinstance(o, dict):
            v = o.get('name')
            if isinstance(v, str):
                names.add(v.upper())
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(j)
    return names, None

def load_keywords(script_dir):
    kw = set()
    candidates = [
        os.path.join(script_dir, 'reference', 'keywords.txt'),
        os.path.join(script_dir, '..', 'reference', 'keywords.txt'),
        os.path.join(script_dir, 'keywords.txt'),
    ]
    for kwfile in candidates:
        if os.path.exists(kwfile):
            for line in open(kwfile, encoding='utf-8'):
                for w in line.split():
                    kw.add(w.upper())
            break
    return kw

# ---------------------------------------------------------------- dict/logic sync
DECL = re.compile(r"\b(numeric|string|list\s+string|list\s+numeric|list|array|"
                  r"function|map|hashmap|valueset|pff|file|document|image|"
                  r"audio|geometry|report)\b", re.I)

def collect_declared(clean):
    """Names introduced by the logic itself: variables, functions, params, valuesets."""
    declared = set()
    # function name + params:  function foo(numeric a, string b)
    for m in re.finditer(r"\bfunction\s+([A-Za-z_]\w*)\s*\(([^)]*)\)", clean, re.I):
        declared.add(m.group(1).upper())
        for p in m.group(2).split(','):
            t = TOKEN.findall(p)
            if t:
                declared.add(t[-1].upper())
    # declarations, incl. comma-separated lists with initializers:
    #   numeric a, b = 1, c;   string s;   list string L;
    for m in re.finditer(r"\b(numeric|string|list\s+string|list\s+numeric|list|array|"
                         r"map|hashmap|valueset|pff|file|document|image|audio|geometry)\s+"
                         r"([^;\n]+)", clean, re.I):
        body = m.group(2)
        for part in body.split(','):
            names = re.findall(r"[A-Za-z_]\w*", part.split('=')[0])
            if names:
                declared.add(names[0].upper())   # the declared name is first in each part
    # do varying numeric i / do varying i
    for m in re.finditer(r"\bvarying\s+(?:numeric\s+|string\s+)?([A-Za-z_]\w*)", clean, re.I):
        declared.add(m.group(1).upper())
    return declared

def sync_check(clean, path, dict_syms, keywords, issues, extra_known, strict=False):
    declared = collect_declared(clean)
    known = dict_syms | keywords | declared | extra_known
    seen = {}
    for m in TOKEN.finditer(clean):
        tok = m.group(0)
        if tok == ';':
            continue
        up = tok.upper()
        if up in known or up in seen:
            continue
        # default: only flag tokens that "look like" a dictionary/form reference
        #   (ALL-CAPS with an underscore -- this project's LI_*, I_*, GEO_* convention).
        # --strict: flag every unknown identifier (also catches lower-case typos, noisier).
        looks_dictish = ('_' in tok) and (tok.upper() == tok) and not tok.isdigit()
        if not (strict or looks_dictish):
            continue
        if tok.isdigit():
            continue
        seen[up] = clean.count('\n', 0, m.start()) + 1
    for up, ln in sorted(seen.items(), key=lambda x: x[1]):
        issues.append((path, ln, 'WARN',
            f"'{up}' is used but not defined in any .dcf/.fmf, declared, or a CSPro keyword"))

def proc_targets(clean):
    """(name, line) for every 'PROC <name>' header in the logic."""
    out = []
    for m in re.finditer(r'(?im)^\s*PROC\s+([A-Za-z_]\w*)', clean):
        out.append((m.group(1).upper(), clean.count('\n', 0, m.start()) + 1))
    return out

# ---------------------------------------------------------------- file parsers
def parse_pff(path):
    sect = None; data = defaultdict(dict)
    for raw in open(path, encoding='utf-8-sig', errors='replace'):
        line = raw.strip()
        if not line or line.startswith(';'):
            continue
        if line.startswith('[') and line.endswith(']'):
            sect = line[1:-1]; continue
        if '=' in line and sect:
            k, v = line.split('=', 1)
            data[sect][k.strip()] = v.strip()
    return data

def load_dict(path):
    """Returns (top_level_name_upper_or_None, symbols_set, error_or_None)."""
    syms, err = load_dict_symbols(path)
    name = None
    raw = open(path, encoding='utf-8-sig', errors='replace').read()
    if raw.lstrip().startswith('{'):
        try:
            name = (json.loads(raw).get('name') or '').upper() or None
        except Exception:
            pass
    else:
        m = re.search(r'^\s*Name\s*=\s*(\S+)', raw, re.M)
        if m:
            name = m.group(1).upper()
    return name, syms, err

def parse_fmf(path):
    """Parse a form file. Returns dict:
       name, dict_files (paths from [Dictionaries]),
       fields  = [(item_upper, dictname_upper_or_None, line)]  from [Field] Item=X,DICT
       form_items = [(item_upper, line)]                        from [Form] Item=X"""
    res = {'name': None, 'dict_files': [], 'fields': [], 'form_items': []}
    sect = None
    for i, raw in enumerate(open(path, encoding='utf-8-sig', errors='replace'), 1):
        s = raw.strip()
        if s.startswith('[') and s.endswith(']'):
            sect = s[1:-1]; continue
        if '=' not in s:
            continue
        k, v = (x.strip() for x in s.split('=', 1))
        if sect == 'FormFile' and k == 'Name' and res['name'] is None:
            res['name'] = v.upper()
        elif sect == 'Dictionaries' and k == 'File':
            res['dict_files'].append(v)
        elif sect == 'Field' and k == 'Item':
            parts = [p.strip() for p in v.split(',')]
            item = parts[0].upper()
            dname = parts[1].upper() if len(parts) > 1 else None
            res['fields'].append((item, dname, i))
        elif sect == 'Form' and k == 'Item':
            res['form_items'].append((v.upper(), i))
    return res

def parse_ent(path):
    """Parse an entry/batch app manifest (.ent JSON). Returns ([(kind, abspath)], err)."""
    refs = []
    try:
        j = json.load(open(path, encoding='utf-8-sig'))
    except Exception as e:
        return refs, str(e)
    base = os.path.dirname(path)
    def add(kind, rel):
        if rel:
            refs.append((kind, os.path.normpath(os.path.join(base, rel))))
    for d in j.get('dictionaries', []):
        add('dictionary', d.get('path'))
    for f in j.get('forms', []):
        add('form', f)
    for c in j.get('code', []):
        add('code', c.get('path') if isinstance(c, dict) else c)
    for key in ('questionText', 'messages'):
        for f in j.get(key, []):
            add(key, f)
    return refs, None

# ---------------------------------------------------------------- cross-file consistency
def consistency_check(project, dcfs, fmfs, ents, pffs, dict_by_name, universe, issues):
    # 1. .ent manifest: every referenced file must exist
    for ent in ents:
        refs, err = parse_ent(ent)
        if err:
            continue
        for kind, p in refs:
            if not os.path.exists(p):
                issues.append((ent, 0, 'ERROR',
                    f"manifest references {kind} '{os.path.basename(p)}' but the file is missing ({p})"))
    # 2. .fmf: [Dictionaries] File must exist; each [Field] Item=X,DICT must exist in DICT
    for fmf in fmfs:
        info = parse_fmf(fmf)
        base = os.path.dirname(fmf)
        for df in info['dict_files']:
            p = os.path.normpath(os.path.join(base, df))
            if not os.path.exists(p):
                issues.append((fmf, 0, 'ERROR',
                    f"[Dictionaries] File={df} -> dictionary not found ({p})"))
        for item, dname, ln in info['fields']:
            if dname and dname in dict_by_name:
                if item not in dict_by_name[dname]:
                    issues.append((fmf, ln, 'ERROR',
                        f"form field binds to Item '{item}' which does not exist in "
                        f"dictionary '{dname}' (dict/form out of sync)"))
            # if dname unknown (external dict not loaded), skip silently
        for item, ln in info['form_items']:
            if item not in universe:
                issues.append((fmf, ln, 'WARN',
                    f"form places item/roster '{item}' that is not a known dictionary item or form"))
    # 3. PFF [ExternalFiles]: name must be a real .dcf top-level name; file must exist
    known_dict_names = set(dict_by_name)
    for pff in pffs:
        data = parse_pff(pff)
        base = os.path.dirname(pff)
        for name, val in data.get('ExternalFiles', {}).items():
            if name.upper() not in known_dict_names:
                issues.append((pff, 0, 'WARN',
                    f"[ExternalFiles] '{name}' is not the top-level name of any .dcf in the project "
                    f"(pff/dictionary out of sync, or dict lives outside the project)"))
            p = os.path.normpath(os.path.join(base, val))
            if not os.path.exists(p):
                issues.append((pff, 0, 'WARN',
                    f"[ExternalFiles] {name}={val} -> data file not found ({p})"))

# ---------------------------------------------------------------- driver
def find(project_dir, ext):
    return glob.glob(os.path.join(project_dir, '**', ext), recursive=True)

def main():
    ap = argparse.ArgumentParser(description="Static pre-check for CSPro applications.")
    ap.add_argument('target', help="project directory or a single .apc file")
    ap.add_argument('--dicts', nargs='*', default=[], help="dictionary dirs/files (single-file mode)")
    ap.add_argument('--balance-only', action='store_true', help="only block/bracket balance")
    ap.add_argument('--strict', action='store_true',
                    help="flag EVERY unknown identifier (also lower-case typos; noisier)")
    ap.add_argument('--no-consistency', action='store_true',
                    help="skip cross-file (.ent/.fmf/.pff) consistency checks")
    args = ap.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    keywords = load_keywords(script_dir)

    if os.path.isdir(args.target):
        project = args.target
        apcs = find(project, '*.apc')
        dcfs = find(project, '*.dcf')
        pffs = find(project, '*.pff')
        fmfs = find(project, '*.fmf')
        ents = find(project, '*.ent')
    else:
        project = os.path.dirname(args.target)
        apcs = [args.target]
        dcfs = []
        for d in args.dicts:
            dcfs += find(d, '*.dcf') if os.path.isdir(d) else [d]
        if not dcfs:
            dcfs = find(project, '*.dcf') + find(os.path.join(project, '..'), '*.dcf')
        pffs = []
        fmfs = find(project, '*.fmf')
        ents = []

    dict_syms = set()          # every symbol from every dictionary
    dict_by_name = {}          # DICTNAME -> its own symbol set
    for d in dcfs:
        name, syms, err = load_dict(d)
        if err:
            print(f"  (could not parse dict {os.path.basename(d)}: {err})")
        dict_syms |= syms
        if name:
            dict_by_name.setdefault(name, set()).update(syms)
    # Form (.fmf) names (form file, form, group, level, roster, field names) are also
    # legitimately referenceable in logic.
    form_syms = set()
    for f in fmfs:
        for m in re.finditer(r'^\s*Name\s*=\s*(\S+)',
                             open(f, encoding='utf-8-sig', errors='replace').read(), re.M):
            form_syms.add(m.group(1).upper())
    universe = dict_syms | form_syms | set(dict_by_name)   # everything a logic name may resolve to

    issues = []
    for apc in apcs:
        src = open(apc, encoding='utf-8-sig', errors='replace').read()
        clean = strip_noise(src)
        check_balance(clean, apc, issues)
        check_brackets(clean, apc, issues)
        if not args.balance_only:
            sync_check(clean, apc, dict_syms, keywords, issues, form_syms | set(dict_by_name),
                       strict=args.strict)
            # orphan PROC targets: a PROC whose field/item/form no longer exists
            for name, ln in proc_targets(clean):
                if name == 'GLOBAL':
                    continue
                if name not in universe and name.upper() not in keywords:
                    issues.append((apc, ln, 'WARN',
                        f"PROC '{name}' targets a name that is not a known dictionary item, "
                        f"field, form, level, or group (orphan PROC / dict-logic out of sync)"))
    if not args.balance_only and not args.no_consistency:
        consistency_check(project, dcfs, fmfs, ents, pffs, dict_by_name, universe, issues)

    errs = [i for i in issues if i[2] == 'ERROR']
    warns = [i for i in issues if i[2] == 'WARN']
    def rel(p):
        try: return os.path.relpath(p, project)
        except Exception: return p
    for path, ln, sev, msg in sorted(issues, key=lambda x: (x[2] != 'ERROR', x[0], x[1])):
        loc = f"{rel(path)}:{ln}" if ln else rel(path)
        print(f"  [{sev}] {loc}: {msg}")
    print(f"\n== {len(errs)} error(s), {len(warns)} warning(s) across "
          f"{len(apcs)} logic file(s), {len(dcfs)} dictionary(ies), "
          f"{len(fmfs)} form(s) ==")
    sys.exit(1 if errs else 0)

if __name__ == '__main__':
    main()
