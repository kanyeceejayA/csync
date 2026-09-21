"""Build the example .csdb that ships with this repo.

    python examples/make_example_csdb.py

Writes `examples/sample_assignments.csdb` from `SAMPLE_ASSIGNMENTS.dcf` with a
handful of assignments in it, so every other example has something to read.
It is a real SQLite file in CSPro's schema - open it with any SQLite client, or
load it with `python -m csync import-csdb examples/sample_assignments.csdb`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # run without installing

from csync import Dictionary, csdb        # noqa: E402

HERE = Path(__file__).resolve().parent
DCF = HERE / "SAMPLE_ASSIGNMENTS.dcf"
OUT = HERE / "sample_assignments.csdb"

# district, ea, staff code, staff name, role, households, status
SEED = [
    (101, 1, "UG-1001", "Aidah Nakato", 1, 120, 2),
    (101, 2, "UG-1001", "Aidah Nakato", 1, 98, 1),
    (101, 3, "UG-1002", "Brian Okello", 1, 140, 3),
    (101, 9, "UG-2001", "Cissy Namara", 2, None, 1),
    (102, 1, "UG-1003", "Denis Wanyama", 1, 87, 1),
    (102, 2, "UG-1003", "Denis Wanyama", 1, 133, 2),
    (102, 4, "UG-1004", "Esther Apio", 1, 111, 1),
    (102, 9, "UG-2001", "Cissy Namara", 2, None, 1),
    (205, 1, "UG-1005", "Fred Mugisha", 1, 156, 1),
    (205, 2, "UG-1006", "Grace Atim", 1, 102, 2),
    (205, 3, "UG-1006", "Grace Atim", 1, 119, 1),
    (205, 9, "UG-2002", "Henry Ssali", 2, None, 1),
]


def main():
    d = Dictionary.load(DCF)
    rows = [{
        "SA_DISTRICT": district, "SA_EA": ea,
        "SA_STAFF_CODE": code, "SA_STAFF_NAME": name, "SA_ROLE": role,
        "SA_HOUSEHOLDS": households, "SA_STATUS": status,
        "SA_DATE_ASSIGNED": 20260901,
    } for district, ea, code, name, role, households, status in SEED]

    csdb.create(OUT, d, rows, overwrite=True)
    print(f"wrote {OUT} - {csdb.schema_info(OUT)}")
    for row in csdb.read_cases(OUT)[:3]:
        print("  ", row["_key"], row["SA_STAFF_NAME"], row["SA_STATUS"])


if __name__ == "__main__":
    main()
