"""Contract tests for the Shopfront mock API (OAuth2 + refresh tokens, cursor pagination).

Shopfront is a fictional e-commerce REST API:

- ``POST /oauth/token`` issues short-lived bearer tokens (``client_credentials``)
  and rotates them (``refresh_token``; each refresh token is single-use).
- ``GET /v1/customers`` and ``GET /v1/orders`` return
  ``{"data": [...], "pagination": {"next_cursor": str | None, "has_more": bool}}``,
  ordered by ``(updated_at, id)`` and filtered with ``updated_since`` (ISO 8601).
- Errors are ``{"error": {"code", "message", "details": [{"param", "message"}]}}``.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mock_apis.shopfront import DEMO_CLIENT_ID, DEMO_CLIENT_SECRET, create_app
from mock_apis.shopfront.data import generate


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock():
    return Clock(datetime(2026, 9, 29, 12, 0, tzinfo=UTC))


@pytest.fixture
def api(clock):
    return TestClient(create_app(clock=clock, token_ttl_seconds=300))


def token(api, **overrides):
    form = {
        "grant_type": "client_credentials",
        "client_id": DEMO_CLIENT_ID,
        "client_secret": DEMO_CLIENT_SECRET,
        **overrides,
    }
    return api.post("/oauth/token", data=form)


@pytest.fixture
def auth(api):
    body = token(api).json()
    return {"Authorization": f"Bearer {body['access_token']}"}


def get_all(api, path, auth, **params):
    """Follow cursors to the end, returning every record and the page count."""
    records, pages = [], 0
    resp = api.get(path, params=params, headers=auth)
    while True:
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        records += body["data"]
        pages += 1
        if not body["pagination"]["has_more"]:
            assert body["pagination"]["next_cursor"] is None
            return records, pages
        cursor = body["pagination"]["next_cursor"]
        resp = api.get(
            path, params={"cursor": cursor, "limit": params.get("limit", 50)}, headers=auth
        )


def error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    return body["error"]


# --- dataset --------------------------------------------------------------------------


def test_dataset_is_deterministic():
    assert generate(seed=7) == generate(seed=7)
    assert generate(seed=7) != generate(seed=8)


# --- authentication: valid ------------------------------------------------------------


def test_client_credentials_issue_bearer_and_refresh_tokens(api):
    resp = token(api)
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 300
    assert body["access_token"] and body["refresh_token"]


def test_refresh_token_issues_new_pair(api):
    first = token(api).json()
    resp = api.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": first["refresh_token"]},
    )
    assert resp.status_code == 200
    second = resp.json()
    assert second["access_token"] != first["access_token"]
    assert second["refresh_token"] != first["refresh_token"]
    headers = {"Authorization": f"Bearer {second['access_token']}"}
    assert api.get("/v1/customers", headers=headers).status_code == 200


# --- authentication: invalid ----------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "status", "code", "message"),
    [
        ({"client_secret": "wrong"}, 401, "invalid_client", "client_id or client_secret"),
        ({"client_id": "nobody"}, 401, "invalid_client", "client_id or client_secret"),
        ({"grant_type": "password"}, 400, "unsupported_grant_type", "client_credentials"),
        ({"client_secret": ""}, 400, "invalid_request", "client_secret"),
    ],
)
def test_token_request_errors(api, overrides, status, code, message):
    resp = token(api, **overrides)
    assert resp.status_code == status
    err = error(resp)
    assert err["code"] == code
    assert message in err["message"]


def test_unknown_refresh_token_is_rejected(api):
    resp = api.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": "nope"})
    assert resp.status_code == 400
    err = error(resp)
    assert err["code"] == "invalid_grant"
    assert "client_credentials" in err["message"]  # tells the caller how to recover


def test_refresh_tokens_are_single_use(api):
    refresh = token(api).json()["refresh_token"]
    form = {"grant_type": "refresh_token", "refresh_token": refresh}
    assert api.post("/oauth/token", data=form).status_code == 200
    resp = api.post("/oauth/token", data=form)
    assert resp.status_code == 400
    assert "already been used" in error(resp)["message"]


def test_missing_bearer_token(api):
    resp = api.get("/v1/customers")
    assert resp.status_code == 401
    err = error(resp)
    assert err["code"] == "missing_token"
    assert "Authorization: Bearer" in err["message"]


def test_invalid_bearer_token(api):
    resp = api.get("/v1/customers", headers={"Authorization": "Bearer made-up"})
    assert resp.status_code == 401
    assert error(resp)["code"] == "invalid_token"


def test_expired_access_token_says_how_to_recover(api, auth, clock):
    clock.advance(seconds=301)
    resp = api.get("/v1/customers", headers=auth)
    assert resp.status_code == 401
    err = error(resp)
    assert err["code"] == "token_expired"
    assert "refresh_token" in err["message"]


# --- valid queries returning data -----------------------------------------------------


@pytest.mark.parametrize("path", ["/v1/customers", "/v1/orders"])
def test_first_page_uses_default_limit(api, auth, path):
    body = api.get(path, headers=auth).json()
    assert len(body["data"]) == 50
    assert body["pagination"]["has_more"] is True
    assert isinstance(body["pagination"]["next_cursor"], str)


@pytest.mark.parametrize(
    ("path", "key"), [("/v1/customers", "customers"), ("/v1/orders", "orders")]
)
def test_cursor_pagination_returns_every_record_once_in_order(api, auth, path, key):
    records, pages = get_all(api, path, auth, limit=37)
    expected = api.app.state.dataset[key]
    assert len(records) == len(expected)
    assert len({r["id"] for r in records}) == len(expected)
    assert records == sorted(records, key=lambda r: (r["updated_at"], r["id"]))
    assert pages == -(-len(expected) // 37)


def test_updated_since_filters_inclusively(api, auth):
    customers = api.app.state.dataset["customers"]
    since = sorted(c["updated_at"] for c in customers)[len(customers) // 2]
    records, _ = get_all(api, "/v1/customers", auth, updated_since=since, limit=100)
    assert {r["id"] for r in records} == {c["id"] for c in customers if c["updated_at"] >= since}
    assert records and min(r["updated_at"] for r in records) == since


def test_updated_since_accepts_offsets(api, auth):
    utc = api.get("/v1/orders", params={"updated_since": "2026-05-01T00:00:00Z"}, headers=auth)
    eastern = api.get(
        "/v1/orders", params={"updated_since": "2026-04-30T20:00:00-04:00"}, headers=auth
    )
    assert utc.json() == eastern.json()


def test_records_have_documented_shape(api, auth):
    customer = api.get("/v1/customers", params={"limit": 1}, headers=auth).json()["data"][0]
    assert set(customer) == {
        "id", "email", "name", "tier", "marketing_opt_in", "lifetime_value",
        "address", "tags", "created_at", "updated_at",
    }  # fmt: skip
    order = api.get("/v1/orders", params={"limit": 1}, headers=auth).json()["data"][0]
    assert set(order) == {
        "id", "customer_id", "status", "currency", "total", "discount_code",
        "line_items", "shipping", "placed_at", "updated_at",
    }  # fmt: skip
    assert order["customer_id"] in {c["id"] for c in api.app.state.dataset["customers"]}


# --- valid queries returning no data --------------------------------------------------


def test_updated_since_in_the_future_returns_empty_page(api, auth):
    resp = api.get("/v1/orders", params={"updated_since": "2099-01-01T00:00:00Z"}, headers=auth)
    assert resp.status_code == 200
    assert resp.json() == {"data": [], "pagination": {"next_cursor": None, "has_more": False}}


def test_empty_dataset_returns_empty_page(clock):
    api = TestClient(create_app(clock=clock, dataset={"customers": [], "orders": []}))
    headers = {"Authorization": f"Bearer {token(api).json()['access_token']}"}
    resp = api.get("/v1/customers", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"] == []
    assert resp.json()["pagination"]["has_more"] is False


# --- invalid queries ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "param", "message"),
    [
        ({"limit": "0"}, "limit", "between 1 and 100"),
        ({"limit": "101"}, "limit", "between 1 and 100"),
        ({"limit": "ten"}, "limit", "must be an integer"),
        ({"updated_since": "last tuesday"}, "updated_since", "ISO 8601"),
        ({"updated_since": "2026-05-01T00:00:00"}, "updated_since", "timezone"),
        ({"cursor": "not-a-cursor"}, "cursor", "next_cursor"),
        ({"page": "2"}, "page", "cursor"),
    ],
)
def test_invalid_parameters_name_the_parameter_and_fix(api, auth, params, param, message):
    resp = api.get("/v1/customers", params=params, headers=auth)
    assert resp.status_code == 400
    err = error(resp)
    assert err["code"] == "invalid_parameters"
    assert [d["param"] for d in err["details"]] == [param]
    assert message in err["details"][0]["message"]


def test_every_invalid_parameter_is_reported_at_once(api, auth):
    params = {"limit": "0", "updated_since": "yesterday", "sort": "name"}
    resp = api.get("/v1/orders", params=params, headers=auth)
    assert resp.status_code == 400
    err = error(resp)
    assert sorted(d["param"] for d in err["details"]) == ["limit", "sort", "updated_since"]
    assert "3 invalid parameters" in err["message"]


def test_unknown_parameter_lists_allowed_ones(api, auth):
    resp = api.get("/v1/orders", params={"status": "paid"}, headers=auth)
    detail = error(resp)["details"][0]
    assert detail["param"] == "status"
    assert "allowed: cursor, limit, updated_since" in detail["message"]


def test_cursor_cannot_be_combined_with_filters(api, auth):
    cursor = api.get("/v1/orders", headers=auth).json()["pagination"]["next_cursor"]
    params = {"cursor": cursor, "updated_since": "2026-01-01T00:00:00Z"}
    resp = api.get("/v1/orders", params=params, headers=auth)
    assert resp.status_code == 400
    detail = error(resp)["details"][0]
    assert detail["param"] == "updated_since"
    assert "cursor already" in detail["message"]


def test_cursor_from_another_endpoint_is_rejected(api, auth):
    cursor = api.get("/v1/orders", headers=auth).json()["pagination"]["next_cursor"]
    resp = api.get("/v1/customers", params={"cursor": cursor}, headers=auth)
    assert resp.status_code == 400
    assert "/v1/orders" in error(resp)["details"][0]["message"]


def test_unknown_endpoint(api, auth):
    resp = api.get("/v1/invoices", headers=auth)
    assert resp.status_code == 404
    err = error(resp)
    assert err["code"] == "not_found"
    assert "/v1/customers" in err["message"]
