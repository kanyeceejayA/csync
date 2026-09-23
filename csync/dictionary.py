"""The CSPro data dictionary (.dcf), and the case shapes built from it.

This is what makes the rest of the package dictionary-agnostic: a .dcf fully
describes a survey's items, so every function elsewhere works from a parsed
`Dictionary` and never hard-codes a field name.

Two on-disk formats exist and both are read here:
  * CSPro 8.x   - JSON: {"software":"CSPro","fileType":"dictionary", ...}
  * CSPro <=7.x - INI:  [Dictionary] / [Level] / [IdItems] / [Record] / [Item]

A *case* is a plain dict, "flat row" style:

    {"AA_MM": 10, "AA_YYYY": 1990, "AA_PHONE": 700000002,      # the id items
     "AA_REGION": 1, "AA_DISTRICT": 102,                       # a single record
     "ROSTER_REC": [{"X": 1}, {"X": 2}]}                       # a repeating one

CSWeb speaks two case formats, chosen by the server's `apiVersion`
(`GET /server`), exactly as CSEntry chooses between its SyncCaseV2 and
SyncCaseV3 serializers:

  V2 - CSWeb 8.0 (apiVersion 2.0), built by `to_level1()`:
    {"id", "caseids", "level-1": "<JSON string>", "deleted", "verified",
     "label", "clock"}
    * `level-1` is a JSON *string* of {"id": {ids}, "REC": {items}}.
    * A single-occurrence record must be an object, NOT a one-element array -
      CSWeb accepts the array with HTTP 200 and then silently mangles the case.

  V3 - CSWeb 8.1 (apiVersion 3.0), built by `to_level_v3()`; the server
  rejects a V2 body (it requires `uuid`, `key` and `clock`):
    {"key", "uuid", "label", "deleted", "verified", "<LEVEL NAME>": {...},
     "clock"}
    * the level is an *object* under the dictionary's level name, not
      "level-1"; the id items sit directly in it (no "id" wrapper);
    * every record is an array of occurrences, even a single one;
    * every value is wrapped: {"ITEM": {"code": value}}.

Both: blank items are omitted; numbers go as numbers, alpha as trimmed
strings; the key (`caseids` / `key`) is fixed width, id values concatenated,
zero-filled or space-padded exactly as the dictionary says.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

NUMERIC, ALPHA = "numeric", "alpha"
BOM = "﻿"


@dataclass
class Item:
    """One dictionary item (a field)."""

    name: str
    label: str
    type: str                           # NUMERIC | ALPHA
    length: int
    decimals: int = 0
    zero_fill: bool = False
    record: str | None = None           # None => it is an id item
    values: list[tuple] | None = None   # [(value, label), ...] from the first value set

    def label_for(self, value) -> str:
        """Value-set label for a value, or the value itself."""
        if value in (None, ""):
            return ""
        for v, lab in self.values or []:
            if str(v) == str(value):
                return lab
        return str(value)


@dataclass
class Record:
    name: str
    label: str
    max: int = 1
    items: list[Item] = field(default_factory=list)

    @property
    def repeating(self) -> bool:
        return self.max > 1


class Dictionary:
    """A parsed .dcf.  Build it with `Dictionary.load(path)` or `.loads(text)`."""

    def __init__(self, name, label, ids, records, text="", path=None, levels=1, level_name=None):
        self.name = name
        self.label = label or name
        self.ids: list[Item] = ids
        self.records: list[Record] = records
        self.text = text            # original file text, for uploading to CSWeb
        self.path = path
        self.levels = levels        # only level 1 is synced (see README limitations)
        self.level_name = level_name or f"{name}_LEVEL"   # the V3 wire key of level 1
        self.by_name: dict[str, Item] = {i.name: i for i in self.all_items()}
        self.key_length = sum(i.length for i in self.ids)

    # ---- loading ------------------------------------------------------------
    @classmethod
    def load(cls, path) -> "Dictionary":
        path = Path(path)
        return cls.loads(path.read_text(encoding="utf-8-sig"), path=path)

    @classmethod
    def loads(cls, text: str, path=None) -> "Dictionary":
        text = text.lstrip(BOM)
        if text.lstrip().startswith("{"):
            return _from_json(json.loads(text), text, path)
        return _from_ini(text, path)

    # ---- lookups ------------------------------------------------------------
    def all_items(self) -> list[Item]:
        out = list(self.ids)
        for r in self.records:
            out.extend(r.items)
        return out

    def record(self, name: str) -> Record | None:
        return next((r for r in self.records if r.name.upper() == name.upper()), None)

    def item(self, name: str) -> Item:
        try:
            return self.by_name[name]
        except KeyError:
            raise KeyError(f"{name!r} is not an item of dictionary {self.name}") from None

    # ---- the case key -------------------------------------------------------
    def key_part(self, item: Item, value) -> str:
        """One id value, padded to its dictionary width."""
        if value in (None, ""):
            return " " * item.length
        if item.type == NUMERIC:
            s = str(int(float(value)))
            return s.rjust(item.length, "0" if item.zero_fill else " ")[-item.length:]
        return str(value)[: item.length].ljust(item.length)

    def build_key(self, row: dict) -> str:
        """The fixed-width case key ("caseids") for a row."""
        return "".join(self.key_part(i, row.get(i.name)) for i in self.ids)

    def parse_key(self, key: str) -> dict:
        """The inverse: split a key back into id values."""
        out, pos = {}, 0
        key = (key or "").ljust(self.key_length)
        for i in self.ids:
            raw = key[pos: pos + i.length]
            pos += i.length
            if not raw.strip():
                out[i.name] = None
            elif i.type != NUMERIC:
                out[i.name] = raw.strip()
            else:
                out[i.name] = float(raw) if i.decimals else int(raw)
        return out

    def key_complete(self, row: dict) -> bool:
        """A case cannot be created without every id item filled in."""
        return all(row.get(i.name) not in (None, "") for i in self.ids)

    # ---- wire format --------------------------------------------------------
    def to_level1(self, row: dict) -> dict:
        """Flat row -> the object that becomes the `level-1` JSON string."""
        level = {"id": _clean(self.ids, row)}
        for r in self.records:
            if r.repeating:
                built = [_clean(r.items, occ) for occ in (row.get(r.name) or [])]
                built = [b for b in built if b]
                if built:
                    level[r.name] = built
            else:
                built = _clean(r.items, row)
                if built:
                    level[r.name] = built          # an object, never [object]
        return level

    def to_level_v3(self, row: dict) -> dict:
        """Flat row -> the V3 (CSWeb 8.1) level object: ids at the top, every
        record an array of occurrences, every value {"code": value}."""
        level = {name: {"code": v} for name, v in _clean(self.ids, row).items()}
        for r in self.records:
            occs = (row.get(r.name) or []) if r.repeating else [row]
            built = [{name: {"code": v} for name, v in _clean(r.items, occ).items()}
                     for occ in occs]
            built = [b for b in built if b]
            if built:
                level[r.name] = built              # always an array in V3
        return level

    def from_case(self, case: dict) -> dict:
        """A CSWeb case -> flat row.  Reads both wire formats (V2 and V3, see
        the module docstring) and tolerates the array/object mix-up on read."""
        level = (case.get("level-1") or case.get("level_1")
                 or case.get(self.level_name) or {})
        if isinstance(level, str):
            try:
                level = json.loads(level)
            except ValueError:
                level = {}
        # V2 wraps the id items in "id"; V3 puts them straight in the level
        ids = level.get("id") if isinstance(level.get("id"), dict) else level
        row = {}
        for i in self.ids:
            row[i.name] = coerce(i, _code(ids.get(i.name)))
        key = case.get("caseids") or case.get("key")
        if all(v in (None, "") for v in row.values()) and key:
            row.update(self.parse_key(key))                  # older servers omit id
        for r in self.records:
            rec = level.get(r.name)
            if r.repeating:
                if rec and not isinstance(rec, list):
                    rec = [rec]
                row[r.name] = [{i.name: coerce(i, _code((occ or {}).get(i.name))) for i in r.items}
                               for occ in (rec or [])]
            else:
                if isinstance(rec, list):
                    rec = rec[0] if rec else None
                for i in r.items:
                    row[i.name] = coerce(i, _code((rec or {}).get(i.name)))
        return row

    def case_body(self, row: dict, guid: str, clock: list, *, deleted=False, label="",
                  api=2) -> dict:
        """The JSON body CSWeb expects for one case.  `api` is the server's
        case format: 2 for CSWeb 8.0, 3 for CSWeb 8.1 (see `CSWeb.case_api`)."""
        if api >= 3:
            body = {"key": self.build_key(row), "uuid": guid}
            if label:
                body["label"] = label
            body["deleted"] = bool(deleted)
            body["verified"] = False
            body[self.level_name] = self.to_level_v3(row)
            body["clock"] = clock
            return body
        return {
            "id": guid,
            "caseids": self.build_key(row),
            "level-1": json.dumps(self.to_level1(row), separators=(",", ":")),
            "deleted": bool(deleted),
            "verified": False,
            "label": label or "",
            "clock": clock,
        }

    # ---- comparison ---------------------------------------------------------
    def diff(self, other: "Dictionary") -> list[str]:
        """Structural differences, e.g. local .dcf vs the one on the server."""
        out, mine, theirs = [], self.by_name, other.by_name
        for name, i in mine.items():
            j = theirs.get(name)
            if not j:
                out.append(f"{name} is missing on the other side")
            elif (i.type, i.length, i.decimals) != (j.type, j.length, j.decimals):
                out.append(f"{name}: {i.type}({i.length},{i.decimals}) "
                           f"vs {j.type}({j.length},{j.decimals})")
        out += [f"{n} exists only on the other side" for n in theirs if n not in mine]
        for r in self.records:
            s = other.record(r.name)
            if s and s.max != r.max:
                out.append(f"{r.name}: max occurrences {r.max} vs {s.max}")
        return out

    def __repr__(self):
        return (f"<Dictionary {self.name} ids={len(self.ids)} "
                f"records={len(self.records)} key={self.key_length}>")


# ---- value handling ---------------------------------------------------------
def coerce(item: Item, raw):
    """Normalise one value for an item.  None means blank.

    Accepts a value-set *label* for convenience ("INTERVIEWER" -> 1), trims
    alpha values to their dictionary length and rejects impossible numbers.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
    if raw == "":
        return None
    if item.type == NUMERIC:
        if isinstance(raw, str) and item.values:
            hit = next((v for v, lab in item.values if lab.lower() == raw.lower()), None)
            if hit is not None:
                raw = hit
        try:
            n = float(str(raw).replace(",", ""))
        except ValueError:
            raise ValueError(f"{item.name}: {raw!r} is not a number") from None
        n = n if item.decimals else int(n)
        digits = item.length - (1 if item.decimals else 0)
        if len(str(abs(int(n)))) > digits:
            raise ValueError(f"{item.name}: {n} needs more than {digits} digits")
        return n
    return str(raw)[: item.length]


def _code(value):
    """A wire value -> the bare value.  V3 wraps it as {"code": v}; a
    multiply-occurring item comes as a list, of which the first is kept."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        return value.get("code")
    return value


def case_guid(case: dict):
    """The GUID of a server case in either format (V3 `uuid`, V2 `id`)."""
    return case.get("uuid") or case.get("id")


def case_key(case: dict) -> str:
    """The fixed-width key of a server case in either format (V3 `key`, V2 `caseids`)."""
    return case.get("key") or case.get("caseids") or ""


def _clean(items, src: dict) -> dict:
    """Items of one record, blanks dropped, ready for the wire."""
    out = {}
    for i in items:
        v = coerce(i, (src or {}).get(i.name))
        if v is not None:
            out[i.name] = v
    return out


# ---- CSPro 8.x JSON ---------------------------------------------------------
def _json_item(raw: dict, record: str | None) -> Item | None:
    if raw.get("itemType") == "subitem":
        return None                       # subitems overlap their parent's columns
    values = None
    for vs in raw.get("valueSets") or []:
        # Discrete values only - a pair can also be {"range": [from, to]},
        # which has no single code to label.
        values = [(v["pairs"][0]["value"], _text(v.get("labels")) or str(v["pairs"][0]["value"]))
                  for v in vs.get("values", [])
                  if v.get("pairs") and "value" in v["pairs"][0]]
        break                             # only the first value set is used for labels
    return Item(
        name=raw["name"],
        label=_text(raw.get("labels")) or raw["name"],
        type=NUMERIC if raw.get("contentType", "numeric") == "numeric" else ALPHA,
        length=int(raw.get("length", 1)),
        decimals=int(raw.get("decimals", 0) or 0),
        zero_fill=bool(raw.get("zeroFill")),
        record=record,
        values=values or None,
    )


def _from_json(doc: dict, text: str, path) -> Dictionary:
    levels = doc.get("levels") or []
    if not levels:
        raise ValueError("dictionary has no levels")
    lvl = levels[0]
    ids = [i for i in (_json_item(x, None) for x in lvl.get("ids", {}).get("items", [])) if i]
    records = []
    for r in lvl.get("records", []):
        items = [i for i in (_json_item(x, r["name"]) for x in r.get("items", [])) if i]
        records.append(Record(r["name"], _text(r.get("labels")) or r["name"],
                              int((r.get("occurrences") or {}).get("maximum", 1)), items))
    return Dictionary(doc["name"], _text(doc.get("labels")), ids, records,
                      text=text, path=path, levels=len(levels), level_name=lvl.get("name"))


def _text(labels) -> str:
    return (labels or [{}])[0].get("text", "") if labels else ""


# ---- CSPro <=7.x INI --------------------------------------------------------
def _from_ini(text: str, path) -> Dictionary:
    """Walk the INI sections in order; [Item]s belong to the section above them."""
    state = {"name": "", "label": "", "zero_fill": False, "target": None, "level_name": None}
    ids: list[Item] = []
    records: list[Record] = []
    section, cur, levels = None, {}, 0

    def flush():
        """Finish the section we just read."""
        if not section:
            return
        if section == "dictionary":
            state["name"] = cur.get("name", "")
            state["label"] = cur.get("label", "")
            state["zero_fill"] = cur.get("zerofill", "no").lower() == "yes"
        elif section in ("level", "iditems"):
            state["target"] = "ids"        # the [Item]s after [IdItems] are the case key
            if section == "level" and state["level_name"] is None:
                state["level_name"] = cur.get("name")      # level 1's name: the V3 wire key
        elif section == "record":
            records.append(Record(cur.get("name", ""), cur.get("label", ""),
                                  int(cur.get("maxrecords", 1) or 1), []))
            state["target"] = records[-1]
        elif section == "item":
            target = state["target"]
            item = Item(
                name=cur.get("name", ""),
                label=cur.get("label", "") or cur.get("name", ""),
                type=NUMERIC if cur.get("datatype", "Numeric").lower() == "numeric" else ALPHA,
                length=int(cur.get("len", 1)),
                decimals=int(cur.get("decimal", 0) or 0),
                zero_fill=cur.get("zerofill",
                                  "yes" if state["zero_fill"] else "no").lower() == "yes",
                record=None if target == "ids" else getattr(target, "name", None),
            )
            if cur.get("itemtype", "").lower() != "subitem":
                (ids if target == "ids" else target.items).append(item)
        elif section == "valueset":
            # A value set belongs to the item immediately above it.
            target = state["target"]
            pairs = [v.split(";", 1) for v in cur.get("value", []) if ";" in v]
            pool = ids if target == "ids" else getattr(target, "items", [])
            if pool and pool[-1].values is None and pairs:
                pool[-1].values = [(_val(v.strip().strip("'")), lab.strip()) for v, lab in pairs]

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            flush()
            section, cur = line[1:-1].strip().lower(), {}
            levels += section == "level"
            continue
        if "=" in line and section:
            k, v = line.split("=", 1)
            k = k.strip().lower()
            if k == "value":                       # value sets repeat this key
                cur.setdefault("value", []).append(v.strip())
            else:
                cur[k] = v.strip()
    flush()
    return Dictionary(state["name"], state["label"], ids, records,
                      text=text, path=path, levels=max(levels, 1), level_name=state["level_name"])


def _val(s: str):
    return int(s) if re.fullmatch(r"-?\d+", s) else s
