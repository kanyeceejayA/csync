"""Reading and writing real CSPro data files (.csdb).

A .csdb is a SQLite database.  CSPro 8 (schema version 3) stores a case in
"expanded" form - one row in `cases`, one in `level-1` for the id items, and
one row per occurrence in a table named after each record:

    cases        id (GUID), key, label, deleted, file_order, last_modified_revision
    level-1      level-1-id, case-id, <one column per id item, lower-cased>
    <record>     <record>-id, level-1-id, occ, <one column per item, lower-cased>
    meta         schema_version, cspro_version, dictionary (the .dcf as JSON), ...
    vector_clock case_id, device, revision   <- why a device does or does not update
    file_revisions / sync_history / notes / binary-*

Because the dictionary is embedded in `meta`, reading a .csdb needs nothing
but the file itself - `read_cases()` works for any survey.

Writing: `append_cases()` adds cases to a .csdb that CSPro made, which is the
safe direction.  `create()` builds one from scratch for demos and tests; CSPro
recomputes its own `dictionary_structure` signature when it opens such a file,
so treat a CSPro- or CSExport-produced file as authoritative for fieldwork.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

from .dictionary import Dictionary

SCHEMA_VERSION = 3
CSPRO_VERSION = "8.0.1"


def _connect(path, read_only=False):
    path = Path(path)
    if read_only:
        uri = "file:{}?mode=ro".format(path.as_posix().replace("?", "%3f"))
        db = sqlite3.connect(uri, uri=True)
    else:
        db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    return db


def read_dictionary(path) -> Dictionary:
    """The dictionary embedded in a .csdb."""
    db = _connect(path, read_only=True)
    try:
        row = db.execute("SELECT dictionary FROM meta").fetchone()
    finally:
        db.close()
    if not row or not row["dictionary"]:
        raise ValueError(f"{path}: no dictionary in the file's meta table")
    return Dictionary.loads(row["dictionary"])


def schema_info(path) -> dict:
    """The file's CSPro schema version and case count - a quick sanity check.

    CSPro 8.0 and 8.1 both write schema version 3; a different number means the
    format moved and the expanded-table reader below may need updating.
    """
    db = _connect(path, read_only=True)
    try:
        meta = db.execute("SELECT schema_version, cspro_version FROM meta").fetchone()
        cases = db.execute("SELECT COUNT(*) n FROM cases").fetchone()["n"]
    finally:
        db.close()
    return {"schema_version": meta["schema_version"], "cspro_version": meta["cspro_version"],
            "cases": cases, "supported": meta["schema_version"] <= SCHEMA_VERSION}


def read_cases(path, *, include_deleted=False) -> list[dict]:
    """Every case in a .csdb, as flat rows ready for `add_cases()`.

    Repeating records come back as a list of occurrence dicts under the record
    name, exactly like the rest of this package expects.
    """
    d = read_dictionary(path)
    out = []
    db = _connect(path, read_only=True)
    try:
        sql = ('SELECT c.id, c.key, c.questionnaire, c.deleted, l."level-1-id" AS lid, l.* '
               'FROM cases c LEFT JOIN "level-1" l ON l."case-id" = c.id')
        if not include_deleted:
            sql += " WHERE c.deleted = 0"
        for case in db.execute(sql + " ORDER BY c.file_order"):
            # Some files keep the whole case as JSON instead of expanded rows.
            if case["questionnaire"]:
                row = d.from_case({"level-1": case["questionnaire"], "caseids": case["key"]})
            else:
                row = {i.name: case[i.name.lower()] for i in d.ids
                       if i.name.lower() in case.keys()}
                for rec in d.records:
                    occs = [dict(r) for r in db.execute(
                        f'SELECT * FROM "{rec.name.lower()}" WHERE "level-1-id"=? ORDER BY occ',
                        (case["lid"],))] if case["lid"] is not None else []
                    values = [{i.name: o.get(i.name.lower()) for i in rec.items} for o in occs]
                    if rec.repeating:
                        row[rec.name] = values
                    elif values:
                        row.update(values[0])
            row["_guid"] = case["id"]
            row["_key"] = case["key"]
            row["_deleted"] = bool(case["deleted"])
            out.append(row)
    finally:
        db.close()
    return out


def import_csdb(session, path, *, name=None, register=False) -> dict:
    """Load a .csdb into the local store, ready to push.

    The dictionary inside the file is cached too, so you can work with it
    without ever downloading one.  `register=True` also uploads that .dcf to
    the server (needed once, before its cases can be sent).
    """
    d = read_dictionary(path)
    session.store.save_dictionary(d.name, d.text, d.label, source=str(path))
    session._dicts[d.name] = d                  # noqa: SLF001 - same package
    if register:
        session.client.add_dictionary(d.text)

    rows = read_cases(path)
    added = 0
    for row in rows:
        guid = row.pop("_guid", None)
        row.pop("_key", None)
        row.pop("_deleted", None)
        clean = {k: v for k, v in row.items() if v is not None}
        session.add_case(name or d.name, clean, guid=guid)
        added += 1
    session.store.log(d.name, "import_csdb", {"file": str(path), "cases": added})
    return {"dictionary": d.name, "cases": added}


# ---- writing ----------------------------------------------------------------
def append_cases(path, rows, *, device="csync", dictionary=None) -> int:
    """Add cases to an existing .csdb (one CSPro made).  Returns how many."""
    d = dictionary or read_dictionary(path)
    db = _connect(path)
    try:
        revision = _new_revision(db, device)
        n = 0
        for row in rows:
            _insert_case(db, d, row, revision, device)
            n += 1
        db.commit()
        return n
    finally:
        db.close()


def create(path, dictionary, rows=(), *, device="csync", overwrite=False) -> Path:
    """Build a .csdb from scratch (demos, tests, seeding a fresh file).

    The schema matches CSPro 8's schema version 3.  CSPro recalculates its own
    `dictionary_structure` signature when it opens the file, so for production
    data prefer a file made by CSPro, CSExport or Excel-to-CSPro (xl2cs).
    """
    d = dictionary if isinstance(dictionary, Dictionary) else Dictionary.load(dictionary)
    path = Path(path)
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"{path} already exists (pass overwrite=True)")
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)

    db = _connect(path)
    try:
        db.executescript(_FIXED_SCHEMA)
        db.execute('CREATE TABLE "level-1" ("level-1-id" INTEGER PRIMARY KEY NOT NULL,'
                   '"case-id" TEXT NOT NULL' + _columns(d.ids) + ")")
        db.execute('CREATE UNIQUE INDEX "level-1-case-id" ON "level-1"("case-id")')
        for rec in d.records:
            table = rec.name.lower()
            db.execute(f'CREATE TABLE "{table}" ("{table}-id" INTEGER PRIMARY KEY NOT NULL,'
                       '"level-1-id" INTEGER NOT NULL,"occ" INTEGER NOT NULL DEFAULT 1'
                       + _columns(rec.items) +
                       ',FOREIGN KEY("level-1-id") REFERENCES "level-1"("level-1-id")'
                       " ON DELETE CASCADE)")
            db.execute(f'CREATE INDEX "{table}-level-1-id" ON "{table}"("level-1-id")')
        # CSPro stores the dictionary as its JSON form plus a structure signature.
        text = d.text if d.text.lstrip().startswith("{") else json.dumps(_as_json(d))
        db.execute("INSERT INTO meta VALUES (?,?,?,?,?)",
                   (SCHEMA_VERSION, CSPRO_VERSION, text,
                    hashlib.md5(text.encode()).hexdigest(), int(time.time())))
        revision = _new_revision(db, device)
        for row in rows:
            _insert_case(db, d, row, revision, device)
        db.commit()
    finally:
        db.close()
    return path


def _columns(items) -> str:
    return "".join(f',"{i.name.lower()}" {"INTEGER" if i.type == "numeric" and not i.decimals else ("REAL" if i.type == "numeric" else "TEXT")}'
                   for i in items)


def _new_revision(db, device) -> int:
    cur = db.execute("INSERT INTO file_revisions (device_id, timestamp) VALUES (?,?)",
                     (device, int(time.time())))
    return cur.lastrowid


def _insert_case(db, d: Dictionary, row: dict, revision: int, device: str):
    """One case: the `cases` row, its id items, and every record occurrence."""
    guid = row.get("_guid") or str(uuid.uuid4())
    key = row.get("_key") or d.build_key(row)
    order = (db.execute("SELECT COALESCE(MAX(file_order), 0) + 1 AS n FROM cases")
             .fetchone()["n"])
    db.execute("INSERT OR REPLACE INTO cases (id, key, label, questionnaire,"
               " last_modified_revision, deleted, file_order, verified)"
               " VALUES (?,?,?,?,?,?,?,0)",
               (guid, key, row.get("_label", ""), "", revision,
                int(bool(row.get("_deleted"))), order))
    db.execute("INSERT OR REPLACE INTO vector_clock (case_id, device, revision)"
               " VALUES (?,?,?)", (guid, device, revision))

    ids = [i for i in d.ids]
    cur = db.execute(
        'INSERT INTO "level-1" ("case-id"{}) VALUES ({})'.format(
            "".join(f',"{i.name.lower()}"' for i in ids), ",".join(["?"] * (len(ids) + 1))),
        [guid] + [row.get(i.name) for i in ids])
    level_id = cur.lastrowid

    for rec in d.records:
        occurrences = row.get(rec.name) if rec.repeating else [
            {i.name: row.get(i.name) for i in rec.items}]
        for n, occ in enumerate(occurrences or [], start=1):
            if not occ or all(v in (None, "") for v in occ.values()):
                continue
            cols = "".join(f',"{i.name.lower()}"' for i in rec.items)
            db.execute(
                f'INSERT INTO "{rec.name.lower()}" ("level-1-id","occ"{cols})'
                f' VALUES ({",".join(["?"] * (len(rec.items) + 2))})',
                [level_id, n] + [occ.get(i.name) for i in rec.items])


def _as_json(d: Dictionary) -> dict:
    """A minimal CSPro 8 JSON dictionary, for when the source .dcf was INI."""
    def item(i):
        out = {"name": i.name, "labels": [{"text": i.label}],
               "contentType": i.type, "length": i.length}
        if i.decimals:
            out["decimals"] = i.decimals
        if i.zero_fill:
            out["zeroFill"] = True
        if i.values:
            out["valueSets"] = [{"name": i.name + "_VS1", "labels": [{"text": i.label}],
                                 "values": [{"labels": [{"text": lab}],
                                             "pairs": [{"value": v}]} for v, lab in i.values]}]
        return out

    return {
        "software": "CSPro", "version": 8.0, "fileType": "dictionary",
        "name": d.name, "labels": [{"text": d.label}],
        "relativePositions": True, "recordType": {"start": 1, "length": 1},
        "levels": [{
            "name": d.name + "_LEVEL", "labels": [{"text": d.label}],
            "ids": {"items": [item(i) for i in d.ids]},
            "records": [{"name": r.name, "labels": [{"text": r.label}],
                         "recordType": str(n + 1),
                         "occurrences": {"required": True, "maximum": r.max},
                         "items": [item(i) for i in r.items]}
                        for n, r in enumerate(d.records)],
        }],
    }


#: Everything in a .csdb that does not depend on the dictionary.
_FIXED_SCHEMA = """
CREATE TABLE meta (schema_version INTEGER NOT NULL, cspro_version TEXT NOT NULL,
  dictionary TEXT NOT NULL, dictionary_structure TEXT NOT NULL,
  dictionary_timestamp INT NOT NULL);
CREATE TABLE file_revisions (id INTEGER NOT NULL PRIMARY KEY, device_id TEXT NOT NULL,
  timestamp INTEGER NOT NULL default (strftime('%s','now')));
CREATE TABLE cases (id TEXT NOT NULL, `key` TEXT NOT NULL, label TEXT,
  questionnaire TEXT NOT NULL, last_modified_revision INTEGER NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0, file_order REAL NOT NULL UNIQUE,
  verified INTEGER NOT NULL DEFAULT 0, partial_save_mode TEXT NULL,
  partial_save_field_name TEXT NULL, partial_save_level_key TEXT NULL,
  partial_save_record_occurrence INTEGER NULL, partial_save_item_occurrence INTEGER NULL,
  partial_save_subitem_occurrence INTEGER NULL,
  FOREIGN KEY(last_modified_revision) REFERENCES file_revisions(id));
CREATE UNIQUE INDEX `cases-id` ON cases(id);
CREATE INDEX `cases-deleted-key-file-order` on cases(deleted, key, file_order);
CREATE INDEX `cases-last-modified-revision-key` on cases(last_modified_revision, key);
CREATE TABLE vector_clock (case_id TEXT, device TEXT, revision INTEGER,
  FOREIGN KEY(case_id) REFERENCES cases(id));
CREATE UNIQUE INDEX `vector-clock-case-id-device` ON vector_clock(case_id, device);
CREATE TABLE notes (case_id TEXT NOT NULL, field_name TEXT NOT NULL, level_key TEXT NOT NULL,
  record_occurrence INTEGER NOT NULL, item_occurrence INTEGER NOT NULL,
  subitem_occurrence INTEGER NOT NULL, content TEXT NOT NULL, operator_id TEXT NOT NULL,
  modified_time INTEGER NOT NULL, FOREIGN KEY(case_id) REFERENCES cases(id));
CREATE INDEX `notes-case-id` ON notes(case_id);
CREATE TABLE sync_history (id INTEGER NOT NULL PRIMARY KEY, file_revision INTEGER NOT NULL,
  device_id TEXT NOT NULL, device_name TEXT, user_name TEXT,
  timestamp INTEGER NOT NULL default (strftime('%s','now')), universe TEXT NULL,
  direction INTEGER NULL, server_revision TEXT NULL, partial INTEGER default 0,
  last_id TEXT NULL default NULL);
CREATE TABLE `binary-data`(`signature` TEXT PRIMARY KEY NOT NULL, `data` BLOB,
  `last_modified_revision` INTEGER,
  FOREIGN KEY(`last_modified_revision`) REFERENCES `file_revisions`(`id`));
CREATE TABLE `case-binary-data`(`id` INTEGER PRIMARY KEY NOT NULL, `case-id` TEXT,
  `binary-data-signature` TEXT,
  FOREIGN KEY(`case-id`) REFERENCES `cases`(`id`) ON DELETE CASCADE);
CREATE TABLE `binary-sync-history`(`id` INTEGER PRIMARY KEY NOT NULL,
  `binary-data-signature` TEXT, `sync-history-id` INTEGER);
CREATE TABLE `binary-sync-history-archive`(`id` INTEGER PRIMARY KEY NOT NULL,
  `binary-sync-history-id` INTEGER, `binary-data-signature` TEXT, `sync-history-id` INTEGER);
"""
