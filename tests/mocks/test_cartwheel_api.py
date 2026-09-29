"""Contract tests for the Cartwheel mock API (HTTP Basic, offset/limit, problem+json).

Cartwheel is a second fictional commerce vendor whose records cover the same
ground as Shopfront's but look nothing alike:

- camelCase fields, integer IDs, split first/last names, flat address fields;
  money in integer cents with mixed currencies; epoch-second timestamps;
  ``Y``/``N`` flags and ``""`` where other APIs send ``null``.
- ``GET /api/v3/customers``, ``/api/v3/orders`` and ``/api/v3/order-items``
  (line items are their own endpoint) return
  ``{"results": [...], "total": int, "offset": int, "limit": int}``.
- Soft-deleted records carry ``isDeleted``/``deletedAt`` and are hidden unless
  ``include_deleted=true``.
- The default ``sort=modified`` is not stable (ties come back in arbitrary order),
  so offset paging over it can duplicate and skip records; ``sort=id`` is stable.
- Errors are RFC 7807 ``application/problem+json``.
"""

import base64

import pytest
from fastapi.testclient import TestClient

from mock_apis.cartwheel import DEMO_PASSWORD, DEMO_USERNAME, create_app
from mock_apis.cartwheel.data import generate

TOKEN = base64.b64encode(f"{DEMO_USERNAME}:{DEMO_PASSWORD}".encode()).decode()
AUTH = {"Authorization": f"Basic {TOKEN}"}


@pytest.fixture(scope="module")
def api():
    return TestClient(create_app())


def page_all(api, path, **params):
    """Offset-page to the end; return every record in the order received."""
    records, offset, limit = [], 0, int(params.pop("limit", 50))
    while True:
        resp = api.get(path, params={**params, "offset": offset, "limit": limit}, headers=AUTH)
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        records += body["results"]
        offset += limit
        if offset >= body["total"]:
            return records


def problem(resp):
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert {"type", "title", "status", "detail"} <= body.keys()
    assert body["status"] == resp.status_code
    return body


def live(rows):
    return [r for r in rows if not r["isDeleted"]]


# --- dataset --------------------------------------------------------------------------


def test_dataset_is_deterministic():
    assert generate(seed=5) == generate(seed=5)
    assert generate(seed=5) != generate(seed=6)


def test_dataset_has_the_documented_quirks(api):
    data = api.app.state.dataset
    assert any(c["isDeleted"] for c in data["customers"])
    assert any(o["isDeleted"] for o in data["orders"])
    assert any(c["loyaltyTier"] == "" for c in data["customers"])
    assert {o["currencyCode"] for o in data["orders"]} == {"USD", "EUR", "GBP"}
    for order in data["orders"]:
        expected = order["subtotalCents"] + order["shippingCents"] - order["discountCents"]
        assert order["totalCents"] == expected


# --- authentication -------------------------------------------------------------------


def test_missing_credentials_challenge_for_basic_auth(api):
    resp = api.get("/api/v3/customers")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == 'Basic realm="Cartwheel"'
    assert "HTTP Basic" in problem(resp)["detail"]


def test_wrong_password(api):
    bad = base64.b64encode(f"{DEMO_USERNAME}:nope".encode()).decode()
    resp = api.get("/api/v3/customers", headers={"Authorization": f"Basic {bad}"})
    assert resp.status_code == 401
    assert "username or password is incorrect" in problem(resp)["detail"]


def test_bearer_tokens_are_not_accepted(api):
    resp = api.get("/api/v3/orders", headers={"Authorization": "Bearer abc"})
    assert resp.status_code == 401
    assert "Basic" in problem(resp)["detail"]


# --- valid queries returning data -----------------------------------------------------


@pytest.mark.parametrize("path", ["/api/v3/customers", "/api/v3/orders", "/api/v3/order-items"])
def test_first_page_defaults(api, path):
    body = api.get(path, headers=AUTH).json()
    key = path.rsplit("/", 1)[1].replace("-", "_")
    assert body["offset"] == 0 and body["limit"] == 50
    assert body["total"] == len(live(api.app.state.dataset[key]))
    assert len(body["results"]) == 50


@pytest.mark.parametrize(
    ("path", "key", "pk"),
    [
        ("/api/v3/customers", "customers", "customerId"),
        ("/api/v3/orders", "orders", "orderId"),
        ("/api/v3/order-items", "order_items", "orderItemId"),
    ],
)
def test_stable_sort_pages_every_live_record_once(api, path, key, pk):
    records = page_all(api, path, sort="id", limit=41)
    expected = live(api.app.state.dataset[key])
    assert [r[pk] for r in records] == sorted(r[pk] for r in expected)


def test_default_sort_is_unstable_across_pages(api):
    """The documented quirk: paging the default sort can duplicate and skip records."""
    records = page_all(api, "/api/v3/orders", limit=7)
    ids = [r["orderId"] for r in records]
    expected = {o["orderId"] for o in live(api.app.state.dataset["orders"])}
    assert len(ids) != len(set(ids)) or set(ids) != expected


def test_soft_deleted_records_only_with_include_deleted(api):
    hidden = page_all(api, "/api/v3/customers", sort="id", limit=250)
    everything = page_all(api, "/api/v3/customers", sort="id", limit=250, include_deleted="true")
    deleted = [c for c in everything if c["isDeleted"]]
    assert deleted and not any(c["isDeleted"] for c in hidden)
    assert len(everything) == len(api.app.state.dataset["customers"])
    assert all(c["deletedAt"] >= c["modified"] - 1 for c in deleted)


def test_modified_since_is_inclusive_unix_seconds(api):
    orders = live(api.app.state.dataset["orders"])
    pivot = sorted(o["modified"] for o in orders)[len(orders) // 2]
    records = page_all(api, "/api/v3/orders", sort="id", limit=250, modified_since=str(pivot))
    assert {r["orderId"] for r in records} == {
        o["orderId"] for o in orders if o["modified"] >= pivot
    }


def test_records_have_documented_shape(api):
    customer = api.get("/api/v3/customers", params={"limit": 1}, headers=AUTH).json()["results"][0]
    assert set(customer) == {
        "customerId", "firstName", "lastName", "emailAddress", "loyaltyTier",
        "marketingConsent", "addrLine1", "addrCity", "addrCountry", "created",
        "modified", "isDeleted", "deletedAt",
    }  # fmt: skip
    assert customer["marketingConsent"] in ("Y", "N")
    order = api.get("/api/v3/orders", params={"limit": 1}, headers=AUTH).json()["results"][0]
    assert set(order) == {
        "orderId", "customerId", "orderState", "currencyCode", "subtotalCents",
        "shippingCents", "discountCents", "totalCents", "promoCode", "placed",
        "modified", "isDeleted", "deletedAt",
    }  # fmt: skip
    item = api.get("/api/v3/order-items", params={"limit": 1}, headers=AUTH).json()["results"][0]
    assert set(item) == {
        "orderItemId", "orderId", "productCode", "quantity", "unitPriceCents",
        "lineTotalCents", "modified", "isDeleted", "deletedAt",
    }  # fmt: skip
    order_ids = {o["orderId"] for o in api.app.state.dataset["orders"]}
    assert all(i["orderId"] in order_ids for i in api.app.state.dataset["order_items"])


# --- valid queries returning no data --------------------------------------------------


def test_modified_since_in_the_future_is_empty(api):
    resp = api.get("/api/v3/customers", params={"modified_since": "4102444800"}, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"results": [], "total": 0, "offset": 0, "limit": 50}


def test_offset_past_the_end_is_empty(api):
    resp = api.get("/api/v3/orders", params={"offset": 100000}, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["results"] == []
    assert resp.json()["total"] == len(live(api.app.state.dataset["orders"]))


def test_empty_dataset():
    api = TestClient(create_app(dataset={"customers": [], "orders": [], "order_items": []}))
    body = api.get("/api/v3/order-items", headers=AUTH).json()
    assert body == {"results": [], "total": 0, "offset": 0, "limit": 50}


# --- invalid queries ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "name", "reason"),
    [
        ({"limit": "0"}, "limit", "between 1 and 250"),
        ({"limit": "251"}, "limit", "between 1 and 250"),
        ({"limit": "lots"}, "limit", "integer"),
        ({"offset": "-5"}, "offset", "0 or greater"),
        ({"modified_since": "2026-01-01T00:00:00Z"}, "modified_since", "unix seconds"),
        ({"sort": "name"}, "sort", "one of: id, modified"),
        ({"include_deleted": "yes"}, "include_deleted", "true or false"),
        ({"page": "2"}, "page", "offset"),
        (
            {"fields": "id"},
            "fields",
            "allowed: include_deleted, limit, modified_since, offset, sort",
        ),
    ],
)
def test_invalid_parameters_are_explained(api, params, name, reason):
    resp = api.get("/api/v3/orders", params=params, headers=AUTH)
    assert resp.status_code == 400
    body = problem(resp)
    assert body["type"] == "https://cartwheel.example/problems/invalid-parameters"
    assert [p["name"] for p in body["invalid_params"]] == [name]
    assert reason in body["invalid_params"][0]["reason"]


def test_every_invalid_parameter_is_reported(api):
    resp = api.get(
        "/api/v3/customers", params={"limit": "0", "offset": "x", "sort": "?"}, headers=AUTH
    )
    body = problem(resp)
    assert sorted(p["name"] for p in body["invalid_params"]) == ["limit", "offset", "sort"]
    assert body["detail"] == "3 parameters are invalid: limit, offset, sort"


def test_unknown_endpoint(api):
    resp = api.get("/api/v3/products", headers=AUTH)
    assert resp.status_code == 404
    body = problem(resp)
    assert body["type"] == "https://cartwheel.example/problems/not-found"
    assert "/api/v3/order-items" in body["detail"]
