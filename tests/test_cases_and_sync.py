"""Adding, removing and syncing cases - all through the dictionary-agnostic API."""
import json

import pytest

NAME = "SAMPLE_ASSIGNMENTS"


# ---- local case handling ----------------------------------------------------
def test_add_a_case(session, row):
    case = session.add_case(NAME, row)
    assert case.key == "10100007"
    assert case.dirty is True and case.on_server is False
    assert session.counts(NAME) == {"total": 1, "dirty": 1, "pending_deletes": 0, "deleted": 0}


def test_add_many_and_report_the_bad_ones(session, row):
    good = dict(row)
    bad = {**row, "SA_EA": None}                     # no key -> cannot be a case
    result = session.add_cases(NAME, [good, bad], on_error="skip")
    assert result["added"] == 1
    assert "SA_EA" in result["failed"][0]["error"]


def test_unknown_items_are_rejected(session, row):
    with pytest.raises(KeyError):
        session.add_case(NAME, {**row, "NOT_AN_ITEM": 1})


def test_update_keeps_the_other_items(session, row):
    session.add_case(NAME, row)
    session.update_case(NAME, "10100007", {"SA_HOUSEHOLDS": 42})
    data = session.get_case(NAME, "10100007").data
    assert data["SA_HOUSEHOLDS"] == 42 and data["SA_STAFF_NAME"] == "Aidah Nakato"


def test_remove_marks_a_tombstone(session, row):
    session.add_case(NAME, row)
    session.remove_case(NAME, "10100007")
    case = session.get_case(NAME, "10100007")
    assert case.deleted is True and case.dirty is True


# ---- syncing ----------------------------------------------------------------
def test_sync_up_sends_the_right_body(session, server, row):
    session.add_case(NAME, row)
    report = session.sync_up(NAME)

    assert report["sent"] == 1 and report["verified"] == 1 and report["created"] == 1
    body = server.posts[0]
    if server.api >= 3:                                  # CSWeb 8.1: V3 case
        assert body["key"] == "10100007" and "uuid" in body and "caseids" not in body
        level = body["SAMPLE_ASSIGNMENTS_LEVEL"]         # keyed by the level name
        assert level["SA_DISTRICT"] == {"code": 101}     # ids at the top, values wrapped
        assert isinstance(level["SAMPLE_ASSIGNMENTS_REC"], list)   # records are arrays
        assert level["SAMPLE_ASSIGNMENTS_REC"][0]["SA_STAFF_NAME"] == {"code": "Aidah Nakato"}
    else:                                                # CSWeb 8.0: V2 case
        assert body["caseids"] == "10100007" and "id" in body
        assert isinstance(json.loads(body["level-1"])["SAMPLE_ASSIGNMENTS_REC"], dict)
    assert body["clock"] == [{"deviceId": "test-device", "revision": 1}]
    assert session.get_case(NAME, "10100007").dirty is False     # clean once confirmed


def test_sync_up_reuses_the_server_guid(session, server, dictionary, row):
    server.seed(NAME, dictionary, [row])                 # the case already exists there
    guid = next(iter(server.cases_by_dict[NAME]))
    session.sync_down(NAME)
    session.update_case(NAME, "10100007", {"SA_STATUS": 3})
    session.sync_up(NAME)
    assert list(server.cases_by_dict[NAME]) == [guid]    # updated, not duplicated
    assert server.posts[-1]["clock"][-1]["deviceId"] == "test-device"


def test_sync_down_fills_the_store(session, server, dictionary, row):
    server.seed(NAME, dictionary, [row, {**row, "SA_EA": 8}])
    report = session.sync_down(NAME)
    assert report["added"] == 2 and report["fetched"] == 2
    assert [c.key for c in session.list_cases(NAME)] == ["10100007", "10100008"]
    assert session.counts(NAME)["dirty"] == 0            # pulled cases are clean


def test_sync_down_keeps_unpushed_local_edits(session, server, dictionary, row):
    server.seed(NAME, dictionary, [row])
    session.sync_down(NAME)
    session.update_case(NAME, "10100007", {"SA_HOUSEHOLDS": 999})

    server.cases_by_dict[NAME] = {}                      # server moved on
    server.seed(NAME, dictionary, [{**row, "SA_HOUSEHOLDS": 500}])
    report = session.sync_down(NAME)

    assert report["kept_local_edits"] == 1
    assert report["conflicts"] == ["10100007"]
    assert session.get_case(NAME, "10100007").data["SA_HOUSEHOLDS"] == 999


def test_a_deletion_goes_up_as_a_tombstone(session, server, dictionary, row):
    server.seed(NAME, dictionary, [row])
    session.sync_down(NAME)
    session.remove_case(NAME, "10100007", push=True)
    assert server.posts[-1]["deleted"] is True
    assert session.get_case(NAME, "10100007").dirty is False


def test_a_local_only_case_is_dropped_when_deleted(session, row):
    session.add_case(NAME, row)                          # never pushed
    session.remove_case(NAME, "10100007")
    session.sync_up(NAME)
    assert session.get_case(NAME, "10100007") is None    # nothing to tombstone


def test_universe_limits_what_comes_down(session, server, dictionary, row):
    server.seed(NAME, dictionary, [row, {**row, "SA_DISTRICT": 205}])
    report = session.sync_down(NAME, universe="101")
    assert report["fetched"] == 1
