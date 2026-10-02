"""Contract tests for the Helpline mock API (HMAC-signed requests, changes feed).

Helpline is a second fictional support-desk vendor covering Ticketdesk's ground:

- Every request is signed: ``X-Helpline-Key``, ``X-Helpline-Timestamp`` (unix
  seconds, within 300s of server time) and ``X-Helpline-Signature`` =
  hex HMAC-SHA256(secret, ``METHOD\\nPATH\\nCANONICAL_QUERY\\nTIMESTAMP``), where the
  canonical query is the parameters sorted and percent-encoded.
- ``GET /v1/cases/changes`` and ``/v1/staff/changes`` are changes feeds: start with
  ``start_time`` (ISO 8601 with offset, inclusive on ``updated_at``), continue with
  ``since_token`` until ``has_more`` is false.
- Changes arrive in commit order. ``updated_at`` is stamped when an edit starts
  and a change becomes visible only when committed (up to ~25 minutes later), so
  records can surface after newer-looking ones: late arrivals.
- Deleted cases appear as tombstones ``{"case_number", "deleted": true, "updated_at"}``.
- Cases identify the requester only by email; labels are one ``;``-joined string;
  effort is in minutes; timestamps carry the agent's local UTC offset.
- Errors are ``{"ok": false, "error", "message", "problems": {param: reason}}``.
"""

import hashlib
import hmac
import time
from datetime import datetime
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from mock_apis.helpline import DEMO_KEY_ID, DEMO_SECRET, create_app
from mock_apis.helpline.data import generate


def signed(path, params=None, *, key=DEMO_KEY_ID, secret=DEMO_SECRET, ts=None, method="GET"):
    params = {k: str(v) for k, v in (params or {}).items()}
    ts = str(int(time.time()) if ts is None else ts)
    query = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(params.items()))
    message = "\n".join([method, path, query, ts]).encode()
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    headers = {
        "X-Helpline-Key": key,
        "X-Helpline-Timestamp": ts,
        "X-Helpline-Signature": signature,
    }
    return {"params": params, "headers": headers}


@pytest.fixture
def api():
    return TestClient(create_app())


def call(api, path, **params):
    return api.get(path, **signed(path, params))


def sync(api, path, **params):
    """Follow the feed to the end; return every change in the order received."""
    changes, resp = [], call(api, path, **params)
    while True:
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        changes += body["changes"]
        if not body["has_more"]:
            return changes, body["next_token"]
        resp = call(api, path, since_token=body["next_token"], limit=params.get("limit", 100))


def failure(resp):
    body = resp.json()
    assert body["ok"] is False and {"error", "message"} <= body.keys(), body
    return body


def ts(value):
    return datetime.fromisoformat(value)


START = "2026-01-01T00:00:00Z"


# --- dataset --------------------------------------------------------------------------


def test_dataset_is_deterministic():
    assert generate(seed=9) == generate(seed=9)
    assert generate(seed=9) != generate(seed=10)


# --- authentication -------------------------------------------------------------------


def test_missing_signature_headers_are_listed(api):
    resp = api.get("/v1/cases/changes", params={"start_time": START})
    assert resp.status_code == 401
    message = failure(resp)["message"]
    for header in ("X-Helpline-Key", "X-Helpline-Timestamp", "X-Helpline-Signature"):
        assert header in message


def test_bad_signature_explains_what_to_sign(api):
    request = signed("/v1/cases/changes", {"start_time": START}, secret="wrong")
    resp = api.get("/v1/cases/changes", **request)
    assert resp.status_code == 401
    body = failure(resp)
    assert body["error"] == "invalid_signature"
    assert "METHOD\\nPATH\\nCANONICAL_QUERY\\nTIMESTAMP" in body["message"]


def test_stale_timestamp_is_rejected_to_prevent_replay(api):
    request = signed("/v1/cases/changes", {"start_time": START}, ts=int(time.time()) - 900)
    resp = api.get("/v1/cases/changes", **request)
    assert resp.status_code == 401
    assert "300" in failure(resp)["message"]


def test_unknown_key(api):
    request = signed("/v1/cases/changes", {"start_time": START}, key="someone-else")
    resp = api.get("/v1/cases/changes", **request)
    assert resp.status_code == 401
    assert failure(resp)["error"] == "unknown_key"


# --- valid queries returning data -----------------------------------------------------


@pytest.mark.parametrize(
    ("path", "key"), [("/v1/cases/changes", "cases"), ("/v1/staff/changes", "staff")]
)
def test_sync_returns_every_change_once(api, path, key):
    changes, token = sync(api, path, start_time=START, limit=37)
    ident = "case_number" if key == "cases" else "staff_id"
    assert sorted(c[ident] for c in changes) == sorted(r[ident] for r in api.app.state.dataset[key])
    assert isinstance(token, str) and token


def test_changes_arrive_in_commit_order_so_updated_at_can_go_backwards(api):
    changes, _ = sync(api, "/v1/cases/changes", start_time=START, limit=500)
    stamps = [ts(c["updated_at"]) for c in changes]
    assert any(later < earlier for earlier, later in zip(stamps, stamps[1:], strict=False))


def test_start_time_is_inclusive_and_offset_aware(api):
    cases = api.app.state.dataset["cases"]
    pivot = sorted(cases, key=lambda c: ts(c["updated_at"]))[len(cases) // 2]["updated_at"]
    changes, _ = sync(api, "/v1/cases/changes", start_time=pivot, limit=500)
    assert {c["case_number"] for c in changes} == {
        c["case_number"] for c in cases if ts(c["updated_at"]) >= ts(pivot)
    }
    utc = call(api, "/v1/cases/changes", start_time="2026-05-01T00:00:00Z").json()
    local = call(api, "/v1/cases/changes", start_time="2026-04-30T19:00:00-05:00").json()
    assert utc["changes"] == local["changes"]


def test_deleted_cases_are_tombstones(api):
    changes, _ = sync(api, "/v1/cases/changes", start_time=START, limit=500)
    tombstones = [c for c in changes if c["deleted"]]
    assert tombstones
    assert all(set(t) == {"case_number", "deleted", "updated_at"} for t in tombstones)


def test_case_shape(api):
    changes, _ = sync(api, "/v1/cases/changes", start_time=START, limit=500)
    case = next(c for c in changes if not c["deleted"])
    assert set(case) == {
        "case_number", "title", "state", "urgency", "channel", "contact", "owner",
        "labels", "csat", "effort_minutes", "opened_at", "updated_at", "deleted",
    }  # fmt: skip
    assert set(case["contact"]) == {"email"}
    assert case["urgency"] in {"P1", "P2", "P3", "P4"}
    offsets = {ts(c["updated_at"]).utcoffset() for c in changes}
    assert len(offsets) > 1  # timestamps carry each agent's local offset


def test_late_commits_become_visible_later(api):
    cases = api.app.state.dataset["cases"]
    cutoff = sorted(c["_committed_at"] for c in cases)[len(cases) // 2]
    api.app.state.visible_until = cutoff
    before, token = sync(api, "/v1/cases/changes", start_time=START, limit=500)
    assert len(before) < len(cases)

    api.app.state.visible_until = None  # time passes; everything is committed
    after, _ = sync(api, "/v1/cases/changes", since_token=token, limit=500)
    assert {c["case_number"] for c in before} | {c["case_number"] for c in after} == {
        c["case_number"] for c in cases
    }


# --- valid queries returning no data --------------------------------------------------


def test_start_time_in_the_future_is_empty(api):
    resp = call(api, "/v1/staff/changes", start_time="2099-01-01T00:00:00Z")
    assert resp.status_code == 200
    body = resp.json()
    assert body["changes"] == [] and body["has_more"] is False and body["next_token"]


def test_token_at_the_end_of_the_feed_is_empty(api):
    _, token = sync(api, "/v1/staff/changes", start_time=START)
    body = call(api, "/v1/staff/changes", since_token=token).json()
    assert body["changes"] == [] and body["has_more"] is False


def test_empty_dataset():
    api = TestClient(create_app(dataset={"cases": [], "staff": []}))
    body = call(api, "/v1/cases/changes", start_time=START).json()
    assert body["changes"] == [] and body["has_more"] is False


# --- invalid queries ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "param", "reason"),
    [
        ({"start_time": START, "limit": "0"}, "limit", "between 1 and 500"),
        ({"start_time": START, "limit": "501"}, "limit", "between 1 and 500"),
        ({"start_time": START, "limit": "all"}, "limit", "integer"),
        ({"start_time": "2026-05-01T00:00:00"}, "start_time", "UTC offset"),
        ({"start_time": "May 1st"}, "start_time", "ISO 8601"),
        ({"since_token": "garbage"}, "since_token", "next_token"),
        ({"start_time": START, "since_token": "x"}, "since_token", "not both"),
        ({}, "start_time", "since_token"),
        ({"start_time": START, "page": "2"}, "page", "since_token"),
    ],
)
def test_invalid_parameters_are_explained(api, params, param, reason):
    resp = call(api, "/v1/cases/changes", **params)
    assert resp.status_code == 400
    body = failure(resp)
    assert body["error"] == "invalid_request"
    assert list(body["problems"]) == [param]
    assert reason in body["problems"][param]


def test_token_from_the_other_feed_is_rejected(api):
    _, token = sync(api, "/v1/staff/changes", start_time=START)
    resp = call(api, "/v1/cases/changes", since_token=token)
    assert resp.status_code == 400
    assert "/v1/staff/changes" in failure(resp)["problems"]["since_token"]


def test_every_problem_is_reported(api):
    resp = call(api, "/v1/cases/changes", start_time="soon", limit="0", fields="x")
    body = failure(resp)
    assert sorted(body["problems"]) == ["fields", "limit", "start_time"]
    assert body["message"] == "3 problems with the request: fields, limit, start_time"


def test_unknown_endpoint(api):
    resp = call(api, "/v1/customers/changes", start_time=START)
    assert resp.status_code == 404
    assert "/v1/cases/changes" in failure(resp)["message"]
