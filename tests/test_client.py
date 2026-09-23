"""The HTTP client against scripted server answers: sign-in shapes, API
version detection, and the delta/paging headers CSWeb actually reads."""
import json

import pytest

from csync.client import CSWeb, CSWebError


def scripted(answers):
    """A `_raw` replacement that records requests and replays answers in order.

    It stands in for the whole round trip, so an authenticated call does not
    sign in first - script a token answer only for tests that call token()."""
    seen = []

    def _raw(self, method, path, body=None, headers=None, auth=True):
        seen.append({"method": method, "path": path, "headers": dict(headers or {}),
                     "body": body})
        status, payload, resp_headers = answers.pop(0)
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return status, text, resp_headers
    return _raw, seen


def token_80():
    return 200, {"access_token": "tok80", "expires_in": 3600, "token_type": "Bearer"}, {}


def token_81():
    return 200, {"user": {"id": "u-1", "roleName": "Standard User"},
                 "credentials": {"access_token": "tok81", "expires_in": 3600,
                                 "token_type": "Bearer"}}, {}


@pytest.mark.parametrize("answer, token, role", [(token_80(), "tok80", None),
                                                 (token_81(), "tok81", "Standard User")])
def test_sign_in_reads_both_token_shapes(monkeypatch, answer, token, role):
    raw, _ = scripted([answer])
    monkeypatch.setattr(CSWeb, "_raw", raw)
    api = CSWeb("http://x/csweb", "u", "p")
    assert api.token() == token and api.role == role


def test_sign_in_failure_is_reported(monkeypatch):
    raw, _ = scripted([(400, {"type": "error", "code": "invalid_request",
                              "message": "Invalid username format."}, {})])
    monkeypatch.setattr(CSWeb, "_raw", raw)
    with pytest.raises(CSWebError, match="Invalid username format"):
        CSWeb("http://x/csweb", "a.b", "p").token()


@pytest.mark.parametrize("version, case_api", [("2.0", 2), (2.0, 2), ("3.0", 3), (3, 3)])
def test_case_format_follows_the_api_version(monkeypatch, version, case_api):
    raw, _ = scripted([(200, {"deviceId": "d", "apiVersion": version}, {})])
    monkeypatch.setattr(CSWeb, "_raw", raw)
    assert CSWeb("http://x", "u", "p").case_api == case_api


def test_delta_and_paging_headers(monkeypatch):
    """Page 2 must carry the revision reached on page 1 with the start-after id -
    the start-after id alone makes CSWeb start again from revision 0."""
    page1 = [{"uuid": "a", "key": "1"}, {"uuid": "b", "key": "2"}]
    page2 = [{"uuid": "c", "key": "3"}]
    raw, seen = scripted([
        (206, page1, {"ETag": "17", "x-csw-chunk-max-revision": "17"}),
        (200, page2, {"ETag": "21", "x-csw-chunk-max-revision": "21"}),
    ])
    monkeypatch.setattr(CSWeb, "_raw", raw)
    cases, etag = CSWeb("http://x", "u", "p").cases("D", since_etag="9", page=2)

    assert [c["uuid"] for c in cases] == ["a", "b", "c"] and etag == "21"
    first, second = seen[0]["headers"], seen[1]["headers"]
    assert first["x-csw-if-revision-exists"] == "9" and "If-Match" not in first
    assert "x-csw-case-range-start-after" not in first
    assert second["x-csw-case-range-start-after"] == "b"
    assert second["x-csw-if-revision-exists"] == "17"


def test_etag_survives_nginx_stripping_it(monkeypatch):
    raw, _ = scripted([(200, [], {"x-csw-chunk-max-revision": "5"})])
    monkeypatch.setattr(CSWeb, "_raw", raw)
    assert CSWeb("http://x", "u", "p").cases("D")[1] == "5"
