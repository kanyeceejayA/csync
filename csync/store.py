"""The local SQLite mirror.

Every case the tool knows about lives here, one row per case, so you can add,
edit and review offline and push when you are ready.  Two hashes make "what
changed" exact:

    server_hash - the case content as the server last confirmed it
    local_hash  - the case content as it is here, now

They differ => the row is *dirty* and `sync_up` will send it.  A pull sets
both; a successful push sets server_hash.

This is NOT a CSPro .csdb - it is this tool's own working store, with a plain
schema you can query with any SQLite client.  (For reading and writing real
.csdb files, see csync/csdb.py.)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS dictionaries (
  name        TEXT PRIMARY KEY,
  label       TEXT NOT NULL DEFAULT '',
  dcf         TEXT NOT NULL,            -- the .dcf text, so we can work offline
  source      TEXT NOT NULL DEFAULT '', -- 'server' or the file it came from
  etag        TEXT,                     -- last case etag, for delta pulls
  updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cases (
  dict        TEXT NOT NULL,
  key         TEXT NOT NULL,            -- fixed-width CSPro case key
  guid        TEXT NOT NULL,
  data        TEXT NOT NULL,            -- flat row, as JSON
  clock       TEXT NOT NULL DEFAULT '[]',
  label       TEXT NOT NULL DEFAULT '',
  deleted     INTEGER NOT NULL DEFAULT 0,
  dirty       INTEGER NOT NULL DEFAULT 0,
  server_hash TEXT,
  local_hash  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (dict, key)
);
CREATE INDEX IF NOT EXISTS cases_dirty ON cases(dict, dirty);
CREATE TABLE IF NOT EXISTS log (
  id     INTEGER PRIMARY KEY,
  at     TEXT NOT NULL,
  dict   TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT ''
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def content_hash(level1: dict) -> str:
    """Stable hash of a case's wire content - the basis of 'has it changed?'."""
    return hashlib.sha1(json.dumps(level1, sort_keys=True,
                                   separators=(",", ":")).encode()).hexdigest()


@dataclass
class CaseRow:
    """One case in the local store."""

    dict: str
    key: str
    guid: str
    data: dict
    clock: list
    label: str = ""
    deleted: bool = False
    dirty: bool = False
    server_hash: str | None = None
    local_hash: str = ""
    updated_at: str = ""

    @property
    def on_server(self) -> bool:
        """True once the server has confirmed this case at least once."""
        return self.server_hash is not None


class Store:
    """The working store.  `Store("data/csync.db")`; close it when done."""

    def __init__(self, path="data/csync.db"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- dictionaries -------------------------------------------------------
    def save_dictionary(self, name, dcf_text, label="", source=""):
        """Cache a .dcf so later runs work without the server."""
        self.db.execute(
            """INSERT INTO dictionaries (name, label, dcf, source, updated_at)
                    VALUES (?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                    label=excluded.label, dcf=excluded.dcf,
                    source=excluded.source, updated_at=excluded.updated_at""",
            (name, label, dcf_text, source, now()))
        self.db.commit()

    def dictionary_text(self, name) -> str | None:
        row = self.db.execute("SELECT dcf FROM dictionaries WHERE name=?", (name,)).fetchone()
        return row["dcf"] if row else None

    def dictionaries(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT name, label, source, etag, updated_at FROM dictionaries ORDER BY name")]

    def get_etag(self, name) -> str | None:
        row = self.db.execute("SELECT etag FROM dictionaries WHERE name=?", (name,)).fetchone()
        return row["etag"] if row else None

    def set_etag(self, name, etag):
        self.db.execute("UPDATE dictionaries SET etag=? WHERE name=?", (etag, name))
        self.db.commit()

    # ---- cases --------------------------------------------------------------
    def put(self, row: CaseRow):
        """Insert or replace one case."""
        row.updated_at = now()
        row.dirty = row.server_hash != row.local_hash
        self.db.execute(
            """INSERT INTO cases (dict, key, guid, data, clock, label, deleted, dirty,
                                  server_hash, local_hash, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(dict, key) DO UPDATE SET
                    guid=excluded.guid, data=excluded.data, clock=excluded.clock,
                    label=excluded.label, deleted=excluded.deleted, dirty=excluded.dirty,
                    server_hash=excluded.server_hash, local_hash=excluded.local_hash,
                    updated_at=excluded.updated_at""",
            (row.dict, row.key, row.guid, json.dumps(row.data), json.dumps(row.clock),
             row.label, int(row.deleted), int(row.dirty), row.server_hash,
             row.local_hash, row.updated_at))
        self.db.commit()
        return row

    def get(self, dict_name, key) -> CaseRow | None:
        r = self.db.execute("SELECT * FROM cases WHERE dict=? AND key=?",
                            (dict_name, key)).fetchone()
        return _row(r) if r else None

    def find(self, dict_name, *, include_deleted=False, dirty_only=False,
             limit=None, offset=0) -> list[CaseRow]:
        sql = "SELECT * FROM cases WHERE dict=?"
        args = [dict_name]
        if not include_deleted:
            sql += " AND deleted=0"
        if dirty_only:
            sql += " AND dirty=1"
        sql += " ORDER BY key"
        if limit:
            sql += " LIMIT ? OFFSET ?"
            args += [int(limit), int(offset)]
        return [_row(r) for r in self.db.execute(sql, args)]

    def counts(self, dict_name) -> dict:
        """A one-glance summary: how many cases, how many waiting to go up."""
        q = lambda w: self.db.execute(  # noqa: E731
            f"SELECT COUNT(*) n FROM cases WHERE dict=? AND {w}", (dict_name,)).fetchone()["n"]
        return {
            "total": q("deleted=0"),
            "dirty": q("dirty=1 AND deleted=0"),
            "pending_deletes": q("dirty=1 AND deleted=1"),
            "deleted": q("deleted=1"),
        }

    def purge(self, dict_name, key=None):
        """Remove rows from the local store only - the server is not touched."""
        if key is None:
            self.db.execute("DELETE FROM cases WHERE dict=?", (dict_name,))
        else:
            self.db.execute("DELETE FROM cases WHERE dict=? AND key=?", (dict_name, key))
        self.db.commit()

    # ---- log ----------------------------------------------------------------
    def log(self, dict_name, action, detail=None):
        self.db.execute("INSERT INTO log (at, dict, action, detail) VALUES (?,?,?,?)",
                        (now(), dict_name or "", action,
                         json.dumps(detail) if detail is not None else ""))
        self.db.commit()

    def history(self, limit=50) -> list[dict]:
        rows = self.db.execute("SELECT * FROM log ORDER BY id DESC LIMIT ?", (int(limit),))
        out = []
        for r in rows:
            entry = dict(r)
            try:
                entry["detail"] = json.loads(entry["detail"]) if entry["detail"] else None
            except ValueError:
                pass
            out.append(entry)
        return out


def _row(r: sqlite3.Row) -> CaseRow:
    return CaseRow(
        dict=r["dict"], key=r["key"], guid=r["guid"],
        data=json.loads(r["data"]), clock=json.loads(r["clock"]),
        label=r["label"], deleted=bool(r["deleted"]), dirty=bool(r["dirty"]),
        server_hash=r["server_hash"], local_hash=r["local_hash"], updated_at=r["updated_at"],
    )
