"""Example module: who is assigned to work where.

The rest of csync is dictionary-agnostic - it never needs to know what an item
*means*.  This file is the opposite, and it is the pattern to copy when you
need domain logic: one small `Fields` block says which items carry the meaning,
and everything below is written in terms of that block.

Point it at a different dictionary by changing `Fields` (see "A different
dictionary" in the README):

    from csync.modules.assignments import Assignments, Fields

    a = Assignments(fields=Fields(
        dictionary="NPHC2024_ASSIGNMENTS",
        area=("AA_REGION", "AA_DISTRICT", "AA_EA"),
        staff_code="AA_STAFF_CODE", role="AA_ROLE",
        assigned_on="AA_DATE_ASSIGNED"))
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date

import csync


@dataclass(frozen=True)
class Fields:
    """Which items of the dictionary carry which meaning.

    `area` are the items that identify the place being assigned; they are
    normally the dictionary's id items, which is what makes one assignment per
    area.  Any field set to None is simply not used.
    """

    dictionary: str = "SAMPLE_ASSIGNMENTS"
    area: tuple[str, ...] = ("SA_DISTRICT", "SA_EA")
    staff_code: str = "SA_STAFF_CODE"
    staff_name: str | None = "SA_STAFF_NAME"
    role: str | None = "SA_ROLE"
    status: str | None = "SA_STATUS"
    workload: str | None = "SA_HOUSEHOLDS"
    assigned_on: str | None = "SA_DATE_ASSIGNED"


class Assignments:
    """Assignment operations on top of the generic case functions."""

    def __init__(self, session=None, fields: Fields = Fields()):
        self.session = session or csync.session()
        self.f = fields
        self.dict = self.session.dictionary(fields.dictionary)

    # ---- helpers ------------------------------------------------------------
    def _area(self, area) -> dict:
        """Accept a dict, a tuple in `Fields.area` order, or a single value."""
        if isinstance(area, dict):
            return {k: area[k] for k in self.f.area}
        if not isinstance(area, (list, tuple)):
            area = (area,)
        if len(area) != len(self.f.area):
            raise ValueError(f"expected {len(self.f.area)} area values "
                             f"({', '.join(self.f.area)}), got {len(area)}")
        return dict(zip(self.f.area, area))

    def _set(self, row: dict, field: str | None, value):
        """Only write items this dictionary actually has."""
        if field and value is not None:
            row[field] = value

    # ---- writing ------------------------------------------------------------
    def assign(self, area, staff_code, *, staff_name=None, role=None, workload=None,
               status=None, when=None):
        """Assign one area to one member of staff (replaces any existing one)."""
        row = self._area(area)
        row[self.f.staff_code] = staff_code
        self._set(row, self.f.staff_name, staff_name)
        self._set(row, self.f.role, role)
        self._set(row, self.f.workload, workload)
        self._set(row, self.f.status, status or "NOT STARTED")
        self._set(row, self.f.assigned_on,
                  int((when or date.today()).strftime("%Y%m%d")) if self.f.assigned_on else None)
        return self.session.add_case(self.dict, row)

    def assign_many(self, rows, **kw):
        """Bulk version.  Each row is `{"area": ..., "staff_code": ..., ...}`."""
        out = []
        for r in rows:
            r = dict(r)
            out.append(self.assign(r.pop("area"), r.pop("staff_code"), **{**kw, **r}))
        return out

    def reassign(self, area, staff_code, **kw):
        """Hand an area to somebody else, keeping everything else as it is."""
        key = self.dict.build_key(self._area(area))
        existing = self.session.get_case(self.dict, key)
        if not existing:
            raise KeyError(f"no assignment for {self._area(area)}")
        changes = {self.f.staff_code: staff_code}
        for field, value in (("staff_name", kw.get("staff_name")), ("role", kw.get("role"))):
            self._set(changes, getattr(self.f, field), value)
        self._set(changes, self.f.assigned_on, int(date.today().strftime("%Y%m%d")))
        return self.session.update_case(self.dict, key, changes)

    def unassign(self, area, *, push=False):
        """Remove an assignment (a tombstone, so tablets drop it too)."""
        return self.session.remove_case(self.dict, self.dict.build_key(self._area(area)),
                                        push=push)

    # ---- reading ------------------------------------------------------------
    def all(self):
        """Every current assignment, as flat rows."""
        return [r.data for r in self.session.list_cases(self.dict)]

    def for_staff(self, staff_code):
        """What one person is assigned to."""
        return [r for r in self.all() if r.get(self.f.staff_code) == staff_code]

    def workload_by_staff(self) -> Counter:
        """How many areas (and, if the dictionary has it, how much work) each has."""
        counts = Counter()
        for row in self.all():
            counts[row.get(self.f.staff_code) or "(unassigned)"] += 1
        return counts

    def summary(self) -> dict:
        """One-glance state, including how much is waiting to be published."""
        rows = self.all()
        status_item = self.dict.by_name.get(self.f.status) if self.f.status else None
        by_status = Counter(
            (status_item.label_for(r.get(self.f.status)) or "(blank)") if status_item else "n/a"
            for r in rows)
        return {
            "dictionary": self.dict.name,
            "assignments": len(rows),
            "staff": len({r.get(self.f.staff_code) for r in rows if r.get(self.f.staff_code)}),
            "by_status": dict(by_status),
            **self.session.counts(self.dict),
        }

    # ---- sync ---------------------------------------------------------------
    def refresh(self, **kw):
        """Pull the latest from the server (field progress, tablet edits)."""
        return self.session.sync_down(self.dict, **kw)

    def publish(self, **kw):
        """Send local changes to the server, where tablets will pick them up."""
        return self.session.sync_up(self.dict, **kw)
