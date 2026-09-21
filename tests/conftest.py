"""Test fixtures: a sample dictionary, a throwaway store, and a fake server."""
import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from csync import Dictionary, Session, Settings, Store   # noqa: E402

DCF = Path(__file__).resolve().parents[1] / "examples" / "SAMPLE_ASSIGNMENTS.dcf"


class FakeCSWeb:
    """Enough of the CSWeb API to exercise sync without a server.

    Cases are kept exactly as they arrive on the wire, so the tests also check
    that we send the shapes CSWeb expects.
    """

    def __init__(self, dictionaries=None):
        self.dicts = dict(dictionaries or {})     # name -> .dcf text
        self.cases_by_dict: dict[str, dict] = {}  # name -> {guid: case}
        self.posts = []                           # every body we were sent
        self.revision = 0

    # -- the bits Session uses ------------------------------------------------
    def server(self):
        return {"deviceId": "fake", "apiVersion": "2.0"}

    def dictionaries(self):
        return [{"name": n, "label": n, "caseCount": len(self.cases_by_dict.get(n, {}))}
                for n in self.dicts]

    def dictionary_text(self, name):
        return self.dicts[name]

    def add_dictionary(self, text):
        self.dicts[json.loads(text)["name"]] = text
        return {"code": 200, "description": "Success"}

    def cases(self, name, *, universe=None, since_etag=None, page=100000, limit=None):
        rows = list(self.cases_by_dict.get(name, {}).values())
        if universe:
            rows = [c for c in rows if c["caseids"].startswith(universe)]
        return rows, f"etag-{self.revision}"

    def post_cases(self, name, bodies, *, batch=200, on_progress=None):
        self.posts.extend(bodies)
        self.revision += 1
        store = self.cases_by_dict.setdefault(name, {})
        for body in bodies:
            store[body["id"]] = dict(body)
        return len(bodies)

    # -- helpers for tests ----------------------------------------------------
    def seed(self, name, dictionary, rows):
        """Put cases on the 'server' the way a tablet would."""
        store = self.cases_by_dict.setdefault(name, {})
        for row in rows:
            guid = str(uuid.uuid4())
            store[guid] = dictionary.case_body(
                row, guid, [{"deviceId": "tablet", "revision": 1}])
        return store


@pytest.fixture
def dictionary():
    return Dictionary.load(DCF)


@pytest.fixture
def server(dictionary):
    return FakeCSWeb({dictionary.name: dictionary.text})


@pytest.fixture
def session(tmp_path, server):
    settings = Settings(url="http://fake/api", user="t", password="t", device="test-device",
                        store=str(tmp_path / "store.db"), dict_dir=str(DCF.parent))
    s = Session(settings, store=Store(str(tmp_path / "store.db")), client=server)
    yield s
    s.close()


@pytest.fixture
def row():
    """A complete, valid case."""
    return {"SA_DISTRICT": 101, "SA_EA": 7, "SA_STAFF_CODE": "UG-1001",
            "SA_STAFF_NAME": "Aidah Nakato", "SA_ROLE": 1, "SA_HOUSEHOLDS": 120,
            "SA_STATUS": 1, "SA_DATE_ASSIGNED": 20260901}
