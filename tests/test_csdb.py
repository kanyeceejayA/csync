"""Reading and writing CSPro .csdb files."""
from csync import csdb

NAME = "SAMPLE_ASSIGNMENTS"


def test_create_and_read_back(tmp_path, dictionary, row):
    path = csdb.create(tmp_path / "x.csdb", dictionary, [row, {**row, "SA_EA": 8}])
    info = csdb.schema_info(path)
    assert info == {"schema_version": 3, "cspro_version": "8.0.1",
                    "cases": 2, "supported": True}

    cases = csdb.read_cases(path)
    assert [c["_key"] for c in cases] == ["10100007", "10100008"]
    assert cases[0]["SA_STAFF_NAME"] == "Aidah Nakato"
    assert cases[0]["SA_ROLE"] == 1


def test_the_dictionary_travels_inside_the_file(tmp_path, dictionary, row):
    path = csdb.create(tmp_path / "x.csdb", dictionary, [row])
    embedded = csdb.read_dictionary(path)
    assert embedded.name == dictionary.name
    assert [i.name for i in embedded.ids] == [i.name for i in dictionary.ids]


def test_import_into_the_store_keeps_guids(tmp_path, session, dictionary, row):
    path = csdb.create(tmp_path / "x.csdb", dictionary, [row])
    guid = csdb.read_cases(path)[0]["_guid"]

    result = csdb.import_csdb(session, path)
    assert result == {"dictionary": NAME, "cases": 1}
    case = session.get_case(NAME, "10100007")
    assert case.guid == guid and case.dirty is True     # imported, not yet sent


def test_append_to_an_existing_file(tmp_path, dictionary, row):
    path = csdb.create(tmp_path / "x.csdb", dictionary, [row])
    csdb.append_cases(path, [{**row, "SA_EA": 9}], dictionary=dictionary)
    assert csdb.schema_info(path)["cases"] == 2
