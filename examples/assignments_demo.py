"""End-to-end tour of the library - works with no server at all.

    python examples/assignments_demo.py            # offline: local store only
    python examples/assignments_demo.py --sync     # also sync with CSWeb (.env)

It uses its own store (examples/data/demo.db), so it never touches the one your
real work uses.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # run without installing

import csync                                          # noqa: E402
from csync import csdb                                # noqa: E402
from csync.modules.assignments import Assignments     # noqa: E402

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_assignments.csdb"


def main(sync=False):
    if not SAMPLE.exists():
        raise SystemExit("run examples/make_example_csdb.py first")

    # A session with its own settings: same class the .env would build.
    settings = csync.Settings.from_env()
    settings.store = str(HERE / "data" / "demo.db")
    settings.dict_dir = str(HERE)                       # find SAMPLE_ASSIGNMENTS.dcf here
    session = csync.use(csync.Session(settings))

    # 1. Load the example CSPro data file into the local store.
    print("1. import a .csdb")
    print("  ", csdb.import_csdb(session, SAMPLE))

    # 2. Generic case functions - these work for ANY dictionary.
    print("\n2. generic case functions")
    csync.add_case("SAMPLE_ASSIGNMENTS", {
        "SA_DISTRICT": 303, "SA_EA": 7,
        "SA_STAFF_CODE": "UG-1009", "SA_STAFF_NAME": "Irene Batte",
        "SA_ROLE": "INTERVIEWER",          # a value-set label works as well as 1
        "SA_HOUSEHOLDS": 95, "SA_STATUS": "NOT STARTED",
    })
    csync.add_cases("SAMPLE_ASSIGNMENTS", [
        {"SA_DISTRICT": 303, "SA_EA": 8, "SA_STAFF_CODE": "UG-1009",
         "SA_STAFF_NAME": "Irene Batte", "SA_ROLE": 1, "SA_HOUSEHOLDS": 88},
        {"SA_DISTRICT": 303, "SA_EA": 9, "SA_STAFF_CODE": "UG-2003",
         "SA_STAFF_NAME": "James Odong", "SA_ROLE": "SUPERVISOR"},
    ])
    csync.update_case("SAMPLE_ASSIGNMENTS", "30300007", {"SA_HOUSEHOLDS": 101})
    csync.remove_case("SAMPLE_ASSIGNMENTS", "10100009")     # tombstone, sent on push
    print("   counts:", csync.counts("SAMPLE_ASSIGNMENTS"))

    # 3. The example domain module, written on top of those functions.
    print("\n3. the assignments module")
    a = Assignments(session)
    a.assign((404, 1), "UG-1010", staff_name="Kevin Osoro", role="INTERVIEWER", workload=130)
    a.assign_many([
        {"area": (404, 2), "staff_code": "UG-1010", "staff_name": "Kevin Osoro", "workload": 90},
        {"area": (404, 3), "staff_code": "UG-1011", "staff_name": "Lydia Akello", "workload": 77},
    ], role="INTERVIEWER")
    a.reassign((404, 2), "UG-1011", staff_name="Lydia Akello")
    a.unassign((404, 3))
    print("   summary:", a.summary())
    print("   workload:", dict(a.workload_by_staff().most_common(5)))
    print("   Kevin has:", [r["SA_EA"] for r in a.for_staff("UG-1010")])

    # 4. Sync.  Everything above is local until this point.
    if sync:
        print("\n4. sync with CSWeb")
        print("   register:", session.register_dictionary(HERE / "SAMPLE_ASSIGNMENTS.dcf"))
        print("   down:", session.sync_down("SAMPLE_ASSIGNMENTS"))
        print("   up:  ", session.sync_up("SAMPLE_ASSIGNMENTS"))
    else:
        pending = csync.counts("SAMPLE_ASSIGNMENTS")
        print(f"\n4. {pending['dirty']} case(s) and {pending['pending_deletes']} deletion(s) "
              "are waiting to go up.\n   Configure .env and re-run with --sync to send them.")
    csync.close()


if __name__ == "__main__":
    main(sync="--sync" in sys.argv)
