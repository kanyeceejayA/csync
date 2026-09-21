"""The dictionary parser and the wire format."""
import json

import pytest

from csync import Dictionary

INI_DCF = """[Dictionary]
Version=CSPro 7.7
Label=Tiny
Name=TINY
RecordTypeStart=1
RecordTypeLen=1
ZeroFill=Yes

[Level]
Label=Tiny Level
Name=TINY_LEVEL

[IdItems]

[Item]
Label=Cluster
Name=T_CLUSTER
Start=2
Len=4
DataType=Numeric
ZeroFill=Yes

[Record]
Label=Tiny Record
Name=TINY_REC
RecordTypeValue='1'
MaxRecords=1

[Item]
Label=Name
Name=T_NAME
Start=6
Len=10
DataType=Alpha

[Item]
Label=Sex
Name=T_SEX
Start=16
Len=1
DataType=Numeric

[ValueSet]
Label=Sex
Name=T_SEX_VS1
Value=1;MALE
Value=2;FEMALE
"""


def test_reads_the_json_format(dictionary):
    assert dictionary.name == "SAMPLE_ASSIGNMENTS"
    assert [i.name for i in dictionary.ids] == ["SA_DISTRICT", "SA_EA"]
    assert dictionary.key_length == 8
    assert dictionary.item("SA_ROLE").values == [(1, "INTERVIEWER"), (2, "SUPERVISOR")]


def test_reads_the_ini_format():
    d = Dictionary.loads(INI_DCF)
    assert d.name == "TINY"
    assert [i.name for i in d.ids] == ["T_CLUSTER"]
    assert d.records[0].name == "TINY_REC"
    assert d.item("T_NAME").type == "alpha"
    assert d.item("T_SEX").values == [(1, "MALE"), (2, "FEMALE")]
    assert d.item("T_CLUSTER").zero_fill is True       # inherited from the header


def test_builds_and_parses_the_case_key(dictionary, row):
    key = dictionary.build_key(row)
    assert key == "10100007"                            # zero-filled, fixed width
    assert dictionary.parse_key(key) == {"SA_DISTRICT": 101, "SA_EA": 7}


def test_a_single_record_goes_on_the_wire_as_an_object(dictionary, row):
    level = dictionary.to_level1(row)
    assert level["id"] == {"SA_DISTRICT": 101, "SA_EA": 7}
    assert isinstance(level["SAMPLE_ASSIGNMENTS_REC"], dict)   # NOT a one-item list
    assert level["SAMPLE_ASSIGNMENTS_REC"]["SA_STAFF_CODE"] == "UG-1001"


def test_blank_items_are_left_out(dictionary, row):
    row["SA_HOUSEHOLDS"] = None
    assert "SA_HOUSEHOLDS" not in dictionary.to_level1(row)["SAMPLE_ASSIGNMENTS_REC"]


def test_a_case_survives_the_round_trip(dictionary, row):
    body = dictionary.case_body(row, "guid", [])
    back = dictionary.from_case(body)
    assert back["SA_STAFF_NAME"] == "Aidah Nakato"
    assert back["SA_DISTRICT"] == 101
    assert json.loads(body["level-1"])["id"]["SA_EA"] == 7


def test_values_are_coerced_to_the_dictionary(dictionary):
    from csync import coerce
    assert coerce(dictionary.item("SA_ROLE"), "SUPERVISOR") == 2      # label -> code
    assert coerce(dictionary.item("SA_EA"), "0007") == 7              # text -> number
    assert coerce(dictionary.item("SA_STAFF_NAME"), " x " * 40)[-1] != " "
    with pytest.raises(ValueError):
        coerce(dictionary.item("SA_EA"), "not a number")
    with pytest.raises(ValueError):
        coerce(dictionary.item("SA_DISTRICT"), 12345)                 # too many digits


def test_dictionaries_can_be_compared(dictionary):
    other = Dictionary.loads(dictionary.text.replace('"length": 12', '"length": 20'))
    assert other.diff(dictionary) == ["SA_STAFF_CODE: alpha(20,0) vs alpha(12,0)"]
