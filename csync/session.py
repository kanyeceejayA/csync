"""The working session: a dictionary cache, the local store and the server.

Everything here is dictionary-agnostic.  A .dcf describes the survey in full,
so these functions take a dictionary *name* (or a path to a .dcf) and work out
the rest - no field names are hard-coded anywhere in this file.

    from csync import Session
    s = Session.from_env()
    s.sync_down("MY_DICT")                       # server -> local store
    s.add_case("MY_DICT", {"ID_A": 1, "NAME": "x"})
    s.sync_up("MY_DICT")                         # local store -> server

The module-level shortcuts in `csync/__init__.py` (csync.add_case(...), etc.)
are the same calls on a lazily created default Session.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from .client import CSWeb, CSWebError, bump_clock
from .config import Settings
from .dictionary import Dictionary, case_guid, case_key
from .store import CaseRow, Store, content_hash


class Session:
    """Holds the three things every operation needs: settings, store, client."""

    def __init__(self, settings: Settings | None = None, store: Store | None = None,
                 client: CSWeb | None = None):
        self.settings = settings or Settings()
        self.store = store or Store(self.settings.store)
        self._client = client
        self._dicts: dict[str, Dictionary] = {}

    @classmethod
    def from_env(cls, env_file=None) -> "Session":
        return cls(Settings.from_env(env_file))

    @property
    def client(self) -> CSWeb:
        """The server connection, built on first use."""
        if self._client is None:
            s = self.settings
            if not s.configured:
                raise CSWebError("no server configured - set CSWEB_URL and CSWEB_USER "
                                 "in your .env (offline commands still work)")
            self._client = CSWeb(s.url, s.user, s.password, device=s.device,
                                 timeout=s.timeout, verify_tls=s.verify_tls)
        return self._client

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- dictionaries -------------------------------------------------------
    def dictionary(self, name, *, refresh=False) -> Dictionary:
        """Get a parsed dictionary by name or .dcf path.

        Looked for in this order, so the tool works offline once it has seen a
        dictionary at least once:
          1. a path you passed in;
          2. `<CSYNC_DICTS>/<name>.dcf`;
          3. the copy cached in the local store;
          4. the server (and it is then cached).
        """
        if isinstance(name, Dictionary):
            return name
        key = str(name)
        if not refresh and key in self._dicts:
            return self._dicts[key]

        path = Path(key)
        if path.suffix.lower() == ".dcf" and path.is_file():
            d = Dictionary.load(path)
            self.store.save_dictionary(d.name, d.text, d.label, source=str(path))
        else:
            local = Path(self.settings.dict_dir) / f"{key}.dcf"
            if not refresh and local.is_file():
                d = Dictionary.load(local)
                self.store.save_dictionary(d.name, d.text, d.label, source=str(local))
            else:
                cached = None if refresh else self.store.dictionary_text(key)
                text = cached or self.client.dictionary_text(key)
                d = Dictionary.loads(text)
                if not cached:
                    self.store.save_dictionary(d.name, d.text, d.label, source="server")
        self._dicts[key] = self._dicts[d.name] = d
        return d

    def dictionaries(self) -> list[dict]:
        """Dictionaries on the server, with case counts (falls back to local)."""
        try:
            return self.client.dictionaries()
        except CSWebError:
            return [{"name": d["name"], "label": d["label"], "caseCount": None}
                    for d in self.store.dictionaries()]

    def register_dictionary(self, dcf_path) -> dict:
        """Upload a .dcf so the server (and tablets) know this dictionary."""
        d = Dictionary.load(dcf_path)
        result = self.client.add_dictionary(d.text)
        self.store.save_dictionary(d.name, d.text, d.label, source=str(dcf_path))
        self.store.log(d.name, "register", {"file": str(dcf_path)})
        return {"dictionary": d.name, "result": result}

    def compare_dictionary(self, name) -> list[str]:
        """Differences between the local .dcf and the one on the server."""
        local = self.dictionary(name)
        remote = Dictionary.loads(self.client.dictionary_text(local.name))
        return local.diff(remote)

    # ---- cases: the dictionary-agnostic CRUD --------------------------------
    def add_case(self, name, row: dict, *, label="", guid=None) -> CaseRow:
        """Add one case to the local store (send it later with `sync_up`).

        `row` is a flat dict of item name -> value.  Values may be given as
        value-set labels ("INTERVIEWER") and are coerced to the dictionary's
        type and length.  An existing case with the same key is updated.
        """
        d = self.dictionary(name)
        row = normalize_row(d, row)
        if not d.key_complete(row):
            missing = [i.name for i in d.ids if row.get(i.name) in (None, "")]
            raise ValueError(f"{d.name}: the case key needs {', '.join(missing)}")
        key = d.build_key(row)
        existing = self.store.get(d.name, key)
        return self.store.put(CaseRow(
            dict=d.name, key=key,
            guid=guid or (existing.guid if existing else str(uuid.uuid4())),
            data=row, clock=existing.clock if existing else [],
            label=label or (existing.label if existing else ""),
            deleted=False,
            server_hash=existing.server_hash if existing else None,
            local_hash=content_hash(d.to_level1(row)),
        ))

    def add_cases(self, name, rows, *, on_error="raise") -> dict:
        """Add many cases.  `on_error="skip"` collects failures instead of raising."""
        d = self.dictionary(name)
        added, failed = [], []
        for i, row in enumerate(rows):
            try:
                added.append(self.add_case(d, row))
            except (ValueError, KeyError) as e:
                if on_error == "raise":
                    raise
                failed.append({"index": i, "row": row, "error": str(e)})
        self.store.log(d.name, "add_cases", {"added": len(added), "failed": len(failed)})
        return {"added": len(added), "failed": failed, "cases": added}

    def update_case(self, name, key, changes: dict) -> CaseRow:
        """Change some items of an existing case, keeping the rest."""
        d = self.dictionary(name)
        row = self.store.get(d.name, key)
        if not row:
            raise KeyError(f"{d.name}: no case with key {key!r}")
        merged = dict(row.data)
        merged.update(changes)
        return self.add_case(d, merged, label=row.label, guid=row.guid)

    def remove_case(self, name, key, *, push=False) -> CaseRow:
        """Mark a case deleted.

        It goes up as a *tombstone* (`deleted: true`), which is how CSPro
        removes a case everywhere - a hard DELETE is unreliable on some CSWeb
        builds and leaves tablets holding the old copy.  Pass `push=True` to
        send it straight away.
        """
        d = self.dictionary(name)
        row = self.store.get(d.name, key)
        if not row:
            raise KeyError(f"{d.name}: no case with key {key!r}")
        row.deleted = True
        if not row.local_hash.startswith("deleted:"):   # dirty until the server confirms
            row.local_hash = "deleted:" + row.local_hash
        self.store.put(row)
        self.store.log(d.name, "remove_case", {"key": key.strip()})
        if push:
            self.sync_up(d, keys=[key])
        return row

    def remove_cases(self, name, keys, *, push=False) -> int:
        for key in keys:
            self.remove_case(name, key)
        if push:
            self.sync_up(name, keys=list(keys))
        return len(list(keys))

    def get_case(self, name, key) -> CaseRow | None:
        return self.store.get(self.dictionary(name).name, key)

    def list_cases(self, name, **kw) -> list[CaseRow]:
        return self.store.find(self.dictionary(name).name, **kw)

    def counts(self, name) -> dict:
        return self.store.counts(self.dictionary(name).name)

    # ---- sync ---------------------------------------------------------------
    def sync_down(self, name, *, universe=None, delta=True, keep_local_edits=True) -> dict:
        """Server -> local store.

        `universe` is a CSPro universe expression (usually a case-key prefix)
        that limits what comes down.  `delta=True` asks the server for only
        what changed since the last pull; it falls back to a full pull if the
        server no longer knows our etag.
        """
        d = self.dictionary(name)
        etag = self.store.get_etag(d.name) if (delta and not universe) else None
        try:
            cases, new_etag = self.client.cases(d.name, universe=universe, since_etag=etag)
        except CSWebError as e:
            if e.status != 412:
                raise
            cases, new_etag = self.client.cases(d.name, universe=universe)  # etag too old

        report = {"dictionary": d.name, "fetched": len(cases), "added": 0, "updated": 0,
                  "unchanged": 0, "kept_local_edits": 0, "tombstones": 0, "conflicts": []}
        for case in cases:
            row = d.from_case(case)
            key = case_key(case) or d.build_key(row)
            key = key.ljust(d.key_length)
            server_hash = content_hash(d.to_level1(row))
            deleted = bool(case.get("deleted"))
            report["tombstones"] += deleted
            local = self.store.get(d.name, key)

            if local and local.dirty and keep_local_edits:
                # Someone edited this case here and has not pushed yet: keep
                # the local version, but remember where the server stands.
                if local.server_hash and local.server_hash != server_hash:
                    report["conflicts"].append(key.strip())
                local.guid, local.clock = case_guid(case) or local.guid, case.get("clock") or []
                local.server_hash = server_hash
                self.store.put(local)
                report["kept_local_edits"] += 1
                continue

            if local and local.local_hash == server_hash and local.deleted == deleted:
                report["unchanged"] += 1
            elif local:
                report["updated"] += 1
            else:
                report["added"] += 1
            self.store.put(CaseRow(
                dict=d.name, key=key, guid=case_guid(case) or str(uuid.uuid4()),
                data=row, clock=case.get("clock") or [], label=case.get("label") or "",
                deleted=deleted, server_hash=server_hash, local_hash=server_hash,
            ))

        if new_etag and not universe:
            self.store.set_etag(d.name, new_etag)
        self.store.log(d.name, "sync_down", report)
        return report

    def sync_up(self, name, *, keys=None, boost=False, verify=True, on_progress=None) -> dict:
        """Local store -> server.

        Sends every dirty case (or just `keys`), reusing the GUID the server
        already has for that key so nothing is duplicated, and bumping the
        vector clock so tablets accept the new version.  `boost=True` forces
        the point when a tablet is holding a competing edit - see
        `csync.client.bump_clock`.
        """
        d = self.dictionary(name)
        wanted = {k.ljust(d.key_length) for k in keys} if keys else None
        rows = [r for r in self.store.find(d.name, dirty_only=True, include_deleted=True)
                if wanted is None or r.key in wanted]
        report = {"dictionary": d.name, "sent": 0, "verified": 0, "failed": [],
                  "created": 0, "updated": 0, "deleted": 0}
        if not rows:
            return report

        # What the server holds right now: GUIDs to reuse, clocks to dominate.
        server_cases, _ = self.client.cases(d.name)
        by_key = {case_key(c).ljust(d.key_length): c for c in server_cases}
        api = getattr(self.client, "case_api", 2)     # 3 = CSWeb 8.1 case format

        bodies, sent = [], {}
        for row in rows:
            remote = by_key.get(row.key)
            if row.deleted and not remote:
                self.store.purge(d.name, row.key)     # never reached the server
                continue
            guid = case_guid(remote or {}) or row.guid
            clock = bump_clock((remote or {}).get("clock") or row.clock,
                               self.settings.device, boost)
            bodies.append(d.case_body(row.data, guid, clock,
                                      deleted=row.deleted, label=row.label, api=api))
            sent[row.key] = (row, guid, clock, content_hash(d.to_level1(row.data)))
            report["deleted" if row.deleted else ("updated" if remote else "created")] += 1

        if not bodies:
            return report
        self.client.post_cases(d.name, bodies, batch=self.settings.batch,
                               on_progress=on_progress)
        report["sent"] = len(bodies)

        if not verify:
            for key, (row, guid, clock, hash_) in sent.items():
                row.guid, row.clock, row.server_hash, row.local_hash = guid, clock, hash_, hash_
                self.store.put(row)
            self.store.log(d.name, "sync_up", report)
            return report

        # Read back: HTTP 200 is not proof.  A malformed record shape is
        # accepted with 200 and then stored wrong.
        after, _ = self.client.cases(d.name)
        got = {case_key(c).ljust(d.key_length): c for c in after}
        for key, (row, guid, clock, hash_) in sent.items():
            c = got.get(key)
            ok = bool(c) and case_guid(c) == guid and (
                bool(c.get("deleted")) if row.deleted
                else (not c.get("deleted")
                      and content_hash(d.to_level1(d.from_case(c))) == hash_))
            if not ok:
                report["failed"].append(key.strip())
                continue
            report["verified"] += 1
            row.guid, row.clock, row.server_hash = guid, clock, hash_
            row.local_hash = hash_ if not row.deleted else row.local_hash
            row.dirty = False
            if row.deleted:                      # tombstone confirmed: stop resending
                row.local_hash = row.server_hash
            self.store.put(row)

        self.store.log(d.name, "sync_up", report)
        return report

    def sync(self, name, **kw) -> dict:
        """Down then up - the usual round trip."""
        return {"down": self.sync_down(name, universe=kw.pop("universe", None)),
                "up": self.sync_up(name, **kw)}


def normalize_row(d: Dictionary, row: dict) -> dict:
    """Tidy a user-supplied row: check names, fold flat items into records.

    Items of a *repeating* record may be given at the top level; they become
    that record's single occurrence, which is what most assignment-style
    dictionaries (MaxRecords > 1 but one row per case) actually want.
    """
    known = set(d.by_name) | {r.name for r in d.records}
    unknown = [k for k in row if k not in known]
    if unknown:
        raise KeyError(f"{d.name}: unknown item(s) {', '.join(sorted(unknown))}")

    out = {k: v for k, v in row.items() if k in known}
    for r in d.records:
        if not r.repeating or r.name in out:
            continue
        flat = {i.name: out.pop(i.name) for i in r.items if i.name in out}
        if flat:
            out[r.name] = [flat]
    return out
