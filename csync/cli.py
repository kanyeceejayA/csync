"""Command line: `python -m csync <command>`.

Every command works on any dictionary - the .dcf supplies the field names.
Run `python -m csync --help` for the list, or `python -m csync <cmd> --help`.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys

from . import Session, csdb
from .client import CSWebError


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    session = Session.from_env(args.env)
    try:
        return args.run(session, args) or 0
    except (CSWebError, ValueError, KeyError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        session.close()


# ---- commands ---------------------------------------------------------------
def cmd_check(s, args):
    """Is the configuration usable?"""
    print(f"server   {s.settings.url or '(not set)'}")
    print(f"user     {s.settings.user or '(not set)'}")
    print(f"device   {s.settings.device}")
    print(f"store    {s.store.path}")
    if not s.settings.configured:
        print("\nno server configured - set CSWEB_URL and CSWEB_USER in .env")
        return 1
    info = s.client.server()
    generation = "CSWeb 8.1" if s.client.case_api >= 3 else "CSWeb 8.0"
    print(f"\nconnected: apiVersion {info.get('apiVersion')} ({generation}) "
          f"deviceId {info.get('deviceId')}")
    if s.client.role:                        # only CSWeb 8.1 reports it
        print(f"role     {s.client.role}")
    return 0


def cmd_dicts(s, args):
    for d in s.dictionaries():
        count = d.get("caseCount")
        print(f"{d['name']:<34} {('' if count is None else count):>8}  {d.get('label', '')}")


def cmd_pull(s, args):
    report = s.sync_down(args.dictionary, universe=args.universe, delta=not args.full)
    _print(report)


def cmd_push(s, args):
    keys = args.keys.split(",") if args.keys else None
    report = s.sync_up(args.dictionary, keys=keys, boost=args.boost, verify=not args.no_verify,
                       on_progress=lambda n, total: print(f"  sent {n}/{total}", end="\r"))
    _print(report)


def cmd_sync(s, args):
    _print(s.sync(args.dictionary))


def cmd_ls(s, args):
    d = s.dictionary(args.dictionary)
    rows = s.list_cases(d, include_deleted=args.all, limit=args.limit)
    for r in rows:
        flag = "D" if r.deleted else ("*" if r.dirty else " ")
        print(f"{flag} {r.key}  {json.dumps(r.data, default=str)[:110]}")
    print(f"\n{len(rows)} shown - {s.counts(d)}")


def cmd_show(s, args):
    row = s.get_case(args.dictionary, args.key)
    if not row:
        print("no such case")
        return 1
    print(json.dumps({"key": row.key, "guid": row.guid, "dirty": row.dirty,
                      "deleted": row.deleted, "data": row.data}, indent=2, default=str))


def cmd_add(s, args):
    row = dict(pair.split("=", 1) for pair in args.values)
    case = s.add_case(args.dictionary, row)
    print(f"added {case.key.strip()} (send it with: python -m csync push {args.dictionary})")


def cmd_add_csv(s, args):
    """Column headers must be item names; blank cells are left blank."""
    with open(args.file, newline="", encoding="utf-8-sig") as fh:
        rows = [{k: v for k, v in r.items() if v not in ("", None)}
                for r in csv.DictReader(fh)]
    result = s.add_cases(args.dictionary, rows, on_error="skip")
    print(f"added {result['added']}, failed {len(result['failed'])}")
    for f in result["failed"][:10]:
        print(f"  row {f['index'] + 2}: {f['error']}")


def cmd_rm(s, args):
    s.remove_case(args.dictionary, args.key, push=args.push)
    print("marked deleted" + (" and pushed" if args.push else
                              " - send it with: python -m csync push " + args.dictionary))


def cmd_register(s, args):
    _print(s.register_dictionary(args.file))


def cmd_diff(s, args):
    differences = s.compare_dictionary(args.dictionary)
    print("\n".join(differences) if differences else "local and server dictionaries match")


def cmd_import_csdb(s, args):
    _print(csdb.import_csdb(s, args.file, register=args.register))


def cmd_export_csdb(s, args):
    d = s.dictionary(args.dictionary)
    rows = [{**r.data, "_guid": r.guid, "_key": r.key} for r in s.list_cases(d)]
    path = csdb.create(args.file, d, rows, device=s.settings.device, overwrite=args.force)
    print(f"wrote {len(rows)} cases to {path}")


def cmd_log(s, args):
    for entry in s.store.history(args.limit):
        print(f"{entry['at']}  {entry['dict']:<24} {entry['action']:<12} "
              f"{json.dumps(entry['detail'], default=str)[:90]}")


def cmd_web(s, args):
    from .web import run
    run(s.settings, host=args.host, port=args.port)


def _print(payload):
    print(json.dumps(payload, indent=2, default=str))


# ---- argument parsing -------------------------------------------------------
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m csync", description=__doc__)
    p.add_argument("--env", help="path to a .env file (default: ./.env)")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, fn, help_, dictionary=True):
        sp = sub.add_parser(name, help=help_)
        if dictionary:
            sp.add_argument("dictionary", help="dictionary name, or a path to a .dcf")
        sp.set_defaults(run=fn)
        return sp

    add("check", cmd_check, "check the configuration and the server", dictionary=False)
    add("dicts", cmd_dicts, "list the dictionaries on the server", dictionary=False)

    sp = add("pull", cmd_pull, "server -> local store")
    sp.add_argument("--universe", help="limit to a universe (usually a key prefix)")
    sp.add_argument("--full", action="store_true", help="ignore the delta etag")

    sp = add("push", cmd_push, "local store -> server")
    sp.add_argument("--keys", help="comma-separated case keys (default: everything dirty)")
    sp.add_argument("--boost", action="store_true",
                    help="force past a tablet's competing edit (discards it)")
    sp.add_argument("--no-verify", action="store_true", help="skip the read-back check")

    add("sync", cmd_sync, "pull then push")

    sp = add("ls", cmd_ls, "list cases in the local store")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--all", action="store_true", help="include deleted cases")

    sp = add("show", cmd_show, "show one case")
    sp.add_argument("key")

    sp = add("add", cmd_add, "add a case: add DICT ITEM=VALUE ...")
    sp.add_argument("values", nargs="+", metavar="ITEM=VALUE")

    sp = add("add-csv", cmd_add_csv, "add many cases from a CSV (headers = item names)")
    sp.add_argument("file")

    sp = add("rm", cmd_rm, "mark a case deleted (tombstone)")
    sp.add_argument("key")
    sp.add_argument("--push", action="store_true", help="send it straight away")

    sp = sub.add_parser("register", help="upload a .dcf to the server")
    sp.add_argument("file")
    sp.set_defaults(run=cmd_register)

    add("diff", cmd_diff, "compare the local .dcf with the server's")

    sp = sub.add_parser("import-csdb", help="load a CSPro .csdb into the local store")
    sp.add_argument("file")
    sp.add_argument("--register", action="store_true", help="also upload its dictionary")
    sp.set_defaults(run=cmd_import_csdb)

    sp = add("export-csdb", cmd_export_csdb, "write the local cases to a .csdb")
    sp.add_argument("file")
    sp.add_argument("--force", action="store_true", help="overwrite an existing file")

    sp = sub.add_parser("log", help="what this tool did recently")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(run=cmd_log)

    sp = sub.add_parser("web", help="start the small web front end")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp.set_defaults(run=cmd_web)
    return p
