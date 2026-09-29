"""Contract tests for the Ticketdesk mock API (API key, page-number pagination).

Ticketdesk is a fictional support-desk REST API:

- Every request sends ``X-API-Key``.
- ``GET /api/v2/tickets`` and ``GET /api/v2/agents`` return a bare JSON array,
  ordered by ``(modified_at, id)``, paginated with ``page``/``per_page``. Headers
  carry ``X-Total-Count`` and a ``Link`` header with ``rel="next"`` / ``rel="last"``.
- ``modified_after`` (``YYYY-MM-DD HH:MM:SS``, UTC) is **exclusive**.
- Errors are ``{"message": str, "errors": [{"field", "message"}]}``.
"""

import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from mock_apis.ticketdesk import DEMO_API_KEY, create_app
from mock_apis.ticketdesk.data import generate

KEY = {"X-API-Key": DEMO_API_KEY}


@pytest.fixture(scope="module")
def api():
    return TestClient(create_app())


def links(resp) -> dict[str, dict[str, str]]:
    """Parse the Link header into {rel: query params}."""
    out = {}
    for url, rel in re.findall(r'<([^>]+)>;\s*rel="(\w+)"', resp.headers.get("link", "")):
        out[rel] = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    return out


def get_all(api, path, **params):
    records, resp = [], api.get(path, params=params, headers=KEY)
    while True:
        assert resp.status_code == 200, resp.json()
        records += resp.json()
        nxt = links(resp).get("next")
        if nxt is None:
            return records
        resp = api.get(path, params=nxt, headers=KEY)


def error(resp):
    body = resp.json()
    assert set(body) == {"message", "errors"}, body
    return body


# --- dataset --------------------------------------------------------------------------


def test_dataset_is_deterministic():
    assert generate(seed=3) == generate(seed=3)
    assert generate(seed=3) != generate(seed=4)


# --- authentication -------------------------------------------------------------------


def test_missing_api_key(api):
    resp = api.get("/api/v2/tickets")
    assert resp.status_code == 401
    assert "X-API-Key" in error(resp)["message"]


def test_wrong_api_key(api):
    resp = api.get("/api/v2/tickets", headers={"X-API-Key": "guess"})
    assert resp.status_code == 401
    assert "not valid" in error(resp)["message"]


def test_api_key_in_query_string_is_refused_with_a_hint(api):
    resp = api.get("/api/v2/tickets", params={"api_key": DEMO_API_KEY})
    assert resp.status_code == 401
    assert "header" in error(resp)["message"]


# --- valid queries returning data -----------------------------------------------------


@pytest.mark.parametrize("path", ["/api/v2/tickets", "/api/v2/agents"])
def test_first_page_defaults_and_headers(api, path):
    resp = api.get(path, headers=KEY)
    key = path.rsplit("/", 1)[1]
    total = len(api.app.state.dataset[key])
    assert resp.status_code == 200
    assert len(resp.json()) == min(25, total)
    assert resp.headers["x-total-count"] == str(total)
    assert links(resp)["last"]["page"] == str(-(-total // 25))


@pytest.mark.parametrize(
    ("path", "key"), [("/api/v2/tickets", "tickets"), ("/api/v2/agents", "agents")]
)
def test_following_link_headers_returns_every_record_once_in_order(api, path, key):
    records = get_all(api, path, per_page=40)
    expected = api.app.state.dataset[key]
    assert len(records) == len(expected) == len({r["id"] for r in records})
    assert records == sorted(records, key=lambda r: (r["modified_at"], r["id"]))


def test_link_header_preserves_filters(api):
    resp = api.get(
        "/api/v2/tickets",
        params={"status": "open", "per_page": 5, "modified_after": "2026-02-01 00:00:00"},
        headers=KEY,
    )
    nxt = links(resp)["next"]
    assert nxt == {
        "status": "open",
        "per_page": "5",
        "modified_after": "2026-02-01 00:00:00",
        "page": "2",
    }


def test_last_page_has_no_next_link(api):
    last = links(api.get("/api/v2/tickets", headers=KEY))["last"]
    resp = api.get("/api/v2/tickets", params=last, headers=KEY)
    assert resp.json()
    assert "next" not in links(resp)


def test_modified_after_is_exclusive(api):
    tickets = api.app.state.dataset["tickets"]
    pivot = tickets[len(tickets) // 2]["modified_at"]
    records = get_all(api, "/api/v2/tickets", modified_after=pivot, per_page=200)
    assert {r["id"] for r in records} == {t["id"] for t in tickets if t["modified_at"] > pivot}
    assert all(r["modified_at"] != pivot for r in records)


def test_status_filter(api):
    records = get_all(api, "/api/v2/tickets", status="pending", per_page=200)
    assert records and {r["status"] for r in records} == {"pending"}


def test_active_filter(api):
    records = get_all(api, "/api/v2/agents", active="false")
    assert records and not any(r["active"] for r in records)


def test_records_have_documented_shape(api):
    ticket = api.get("/api/v2/tickets", params={"per_page": 1}, headers=KEY).json()[0]
    assert set(ticket) == {
        "id", "subject", "status", "priority", "requester", "assignee_id", "tags",
        "satisfaction_score", "time_spent_hours", "custom_fields", "created_at", "modified_at",
    }  # fmt: skip
    agent = api.get("/api/v2/agents", params={"per_page": 1}, headers=KEY).json()[0]
    assert set(agent) == {
        "id", "name", "email", "team", "active", "max_open_tickets", "created_at", "modified_at",
    }  # fmt: skip


# --- valid queries returning no data --------------------------------------------------


def test_page_past_the_end_is_empty(api):
    resp = api.get("/api/v2/tickets", params={"page": 999}, headers=KEY)
    assert resp.status_code == 200
    assert resp.json() == []
    assert resp.headers["x-total-count"] == str(len(api.app.state.dataset["tickets"]))
    assert "next" not in links(resp)


def test_modified_after_in_the_future_is_empty(api):
    resp = api.get("/api/v2/agents", params={"modified_after": "2099-01-01 00:00:00"}, headers=KEY)
    assert resp.status_code == 200
    assert resp.json() == []
    assert resp.headers["x-total-count"] == "0"
    assert links(resp) == {}


def test_filter_with_no_matches_is_empty(api):
    resp = api.get(
        "/api/v2/tickets",
        params={"status": "closed", "modified_after": "2099-01-01 00:00:00"},
        headers=KEY,
    )
    assert resp.status_code == 200
    assert resp.json() == []


# --- invalid queries ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "params", "field", "message"),
    [
        ("/api/v2/tickets", {"per_page": "500"}, "per_page", "between 1 and 200"),
        ("/api/v2/tickets", {"per_page": "many"}, "per_page", "whole number"),
        ("/api/v2/tickets", {"page": "0"}, "page", "1 or greater"),
        ("/api/v2/tickets", {"modified_after": "2026-05-01T00:00:00Z"}, "modified_after",
         "YYYY-MM-DD HH:MM:SS"),
        ("/api/v2/tickets", {"modified_after": "2026-13-45 99:00:00"}, "modified_after",
         "not a real date"),
        ("/api/v2/tickets", {"status": "resolved"}, "status",
         "one of: open, pending, solved, closed"),
        ("/api/v2/agents", {"active": "maybe"}, "active", "true or false"),
        ("/api/v2/agents", {"status": "open"}, "status", "allowed: active, modified_after"),
        ("/api/v2/tickets", {"cursor": "abc"}, "cursor", "page"),
    ],
)  # fmt: skip
def test_invalid_parameters_explain_the_fix(api, path, params, field, message):
    resp = api.get(path, params=params, headers=KEY)
    assert resp.status_code == 422
    body = error(resp)
    assert [e["field"] for e in body["errors"]] == [field]
    assert message in body["errors"][0]["message"]


def test_all_invalid_parameters_are_reported_together(api):
    params = {"page": "-1", "per_page": "0", "status": "done"}
    resp = api.get("/api/v2/tickets", params=params, headers=KEY)
    assert resp.status_code == 422
    body = error(resp)
    assert sorted(e["field"] for e in body["errors"]) == ["page", "per_page", "status"]
    assert body["message"] == "Validation failed for 3 parameters: page, per_page, status"


def test_unknown_endpoint(api):
    resp = api.get("/api/v2/users", headers=KEY)
    assert resp.status_code == 404
    assert "/api/v2/tickets" in error(resp)["message"]
