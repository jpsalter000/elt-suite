"""End to end: four mock vendors -> extract-and-load -> dbt -> common models, checked by an oracle.

Two commerce vendors (Shopfront, Cartwheel) and two support vendors (Ticketdesk,
Helpline) deliver the same kinds of records in different shapes. The dbt project
normalizes them into common marts (docs/vendor-variants.md is the spec). The oracle
below is a separate implementation of that spec in plain Python, computed straight
from the mock datasets: if every normalized row matches it, the models implement
the spec.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import elt_suite.contract as contract
from elt_suite.pipeline import load_pipeline, run_pipeline
from mock_apis import cartwheel, helpline, shopfront, ticketdesk

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("WAREHOUSE_DSN"), reason="WAREHOUSE_DSN not set"),
]

ROOT = Path(__file__).parents[1]
SCHEMAS = [
    "globex_shopfront", "umbrella_cartwheel", "initech_ticketdesk", "hooli_helpline",
    "reference", "staging", "intermediate", "marts",
]  # fmt: skip
CENT = Decimal("0.01")

# The spec's vocabularies, restated independently of the dbt seeds.
ORDER_STATUS = {
    **{
        ("shopfront", s): s
        for s in ("pending", "paid", "shipped", "delivered", "cancelled", "refunded")
    },
    ("cartwheel", "AWAITING_PAYMENT"): "pending",
    ("cartwheel", "FULFILLED"): "paid",
    ("cartwheel", "IN_TRANSIT"): "shipped",
    ("cartwheel", "COMPLETE"): "delivered",
    ("cartwheel", "VOIDED"): "cancelled",
    ("cartwheel", "RETURNED"): "refunded",
}  # fmt: skip
TICKET_STATUS = {
    **{("ticketdesk", s): s for s in ("open", "pending", "solved", "closed")},
    ("helpline", "new"): "new",
    ("helpline", "open"): "open",
    ("helpline", "on_hold"): "pending",
    ("helpline", "resolved"): "solved",
    ("helpline", "closed"): "closed",
}  # fmt: skip
PRIORITY = {
    **{("ticketdesk", p): p for p in ("urgent", "high", "normal", "low")},
    ("helpline", "P1"): "urgent",
    ("helpline", "P2"): "high",
    ("helpline", "P3"): "normal",
    ("helpline", "P4"): "low",
}  # fmt: skip
COUNTRY = {"United States": "US", "Canada": "CA", "United Kingdom": "GB", "Germany": "DE"}
TENANT = {
    "shopfront": "globex",
    "cartwheel": "umbrella",
    "ticketdesk": "initech",
    "helpline": "hooli",
}


class Router:
    """One HTTP client that sends each request to the in-process mock its URL's port names."""

    def __init__(self, apps: dict[int, object]) -> None:
        self.clients = {port: TestClient(app) for port, app in apps.items()}

    def _client(self, url: str) -> TestClient:
        return self.clients[httpx.URL(url).port]

    def get(self, url, **kwargs):
        return self._client(url).get(url, **kwargs)

    def post(self, url, **kwargs):
        return self._client(url).post(url, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        pass


@pytest.fixture(scope="module")
def apps():
    return {
        8001: shopfront.create_app(),
        8002: ticketdesk.create_app(),
        8003: cartwheel.create_app(),
        8004: helpline.create_app(),
    }


@pytest.fixture(scope="module")
def warehouse(apps, tmp_path_factory):
    """Run vendor_normalization_daily against the four mocks; yield a connection."""
    import psycopg

    mp = pytest.MonkeyPatch()
    mp.setenv("ELT_HOME", str(ROOT))
    for name, value in {
        "GLOBEX_SHOPFRONT_CLIENT_ID": shopfront.DEMO_CLIENT_ID,
        "GLOBEX_SHOPFRONT_CLIENT_SECRET": shopfront.DEMO_CLIENT_SECRET,
        "INITECH_TICKETDESK_API_KEY": ticketdesk.DEMO_API_KEY,
        "UMBRELLA_CARTWHEEL_USERNAME": cartwheel.DEMO_USERNAME,
        "UMBRELLA_CARTWHEEL_PASSWORD": cartwheel.DEMO_PASSWORD,
        "HOOLI_HELPLINE_KEY_ID": helpline.DEMO_KEY_ID,
        "HOOLI_HELPLINE_SECRET": helpline.DEMO_SECRET,
    }.items():
        mp.setenv(name, value)
    scratch = tmp_path_factory.mktemp("dbt")
    mp.setenv("DBT_TARGET_PATH", str(scratch / "target"))
    mp.setenv("DBT_LOG_PATH", str(scratch / "logs"))
    mp.setattr(contract, "make_client", lambda: Router(apps))

    dsn = os.environ["WAREHOUSE_DSN"]
    with psycopg.connect(dsn, autocommit=True) as conn:
        for schema in SCHEMAS:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")

    results = run_pipeline(load_pipeline("vendor_normalization_daily"))
    failed = {r.task: r.error for r in results if r.status != "succeeded"}
    assert not failed, failed
    with psycopg.connect(dsn) as conn:
        yield conn
    mp.undo()


def rows(conn, sql: str) -> list[tuple]:
    return conn.execute(sql).fetchall()


def key(source: str, native_id: str) -> str:
    return hashlib.md5(f"{source}:{native_id}".encode()).hexdigest()


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT)


def cents(value: int) -> Decimal:
    return (Decimal(value) / 100).quantize(CENT)


def epoch(value: int) -> datetime:
    return datetime.fromtimestamp(value, UTC)


def iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)  # Ticketdesk is naive UTC


# --- commerce --------------------------------------------------------------------------


def oracle_customers(apps):
    expected = {}
    for c in apps[8001].state.dataset["customers"]:
        address = c["address"] or {}
        expected[("shopfront", c["id"])] = (
            c["email"], c["name"], c["marketing_opt_in"], address.get("city"),
            address.get("country"), c["tier"], False,
        )  # fmt: skip
    for c in apps[8003].state.dataset["customers"]:
        expected[("cartwheel", str(c["customerId"]))] = (
            c["emailAddress"], f"{c['firstName']} {c['lastName']}", c["marketingConsent"] == "Y",
            c["addrCity"] or None, COUNTRY.get(c["addrCountry"]), c["loyaltyTier"] or None,
            c["isDeleted"],
        )  # fmt: skip
    return expected


def oracle_orders(apps):
    expected = {}
    for o in apps[8001].state.dataset["orders"]:
        expected[("shopfront", o["id"])] = (
            o["customer_id"], ORDER_STATUS[("shopfront", o["status"])], o["currency"],
            money(o["total"]), money(o["shipping"]["cost"]), None, o["discount_code"],
            iso(o["placed_at"]), iso(o["updated_at"]),
        )  # fmt: skip
    for o in apps[8003].state.dataset["orders"]:
        if o["isDeleted"]:
            continue  # facts hold live records only
        expected[("cartwheel", str(o["orderId"]))] = (
            str(o["customerId"]), ORDER_STATUS[("cartwheel", o["orderState"])], o["currencyCode"],
            cents(o["totalCents"]), cents(o["shippingCents"]), cents(o["discountCents"]),
            o["promoCode"] or None, epoch(o["placed"]), epoch(o["modified"]),
        )  # fmt: skip
    return expected


def oracle_order_lines(apps):
    expected = {}
    for o in apps[8001].state.dataset["orders"]:
        for n, item in enumerate(o["line_items"], start=1):
            unit = money(item["unit_price"])
            expected[("shopfront", f"{o['id']}-{n}")] = (
                o["id"], n, item["sku"], item["quantity"], unit, unit * item["quantity"], "USD",
            )  # fmt: skip
    live = {o["orderId"]: o for o in apps[8003].state.dataset["orders"] if not o["isDeleted"]}
    items = sorted(apps[8003].state.dataset["order_items"], key=lambda i: i["orderItemId"])
    numbers: dict[int, int] = {}
    for i in items:
        if i["isDeleted"] or i["orderId"] not in live:
            continue
        numbers[i["orderId"]] = numbers.get(i["orderId"], 0) + 1
        expected[("cartwheel", str(i["orderItemId"]))] = (
            str(i["orderId"]), numbers[i["orderId"]], i["productCode"], i["quantity"],
            cents(i["unitPriceCents"]), cents(i["lineTotalCents"]),
            live[i["orderId"]]["currencyCode"],
        )  # fmt: skip
    return expected


def test_customers_are_normalized(warehouse, apps):
    actual = {
        (r[0], r[1]): r[2:]
        for r in rows(
            warehouse,
            "select source_system, customer_native_id, email, full_name, marketing_opt_in, "
            "city, country_code, vendor_tier, is_deleted from marts.dim_commerce_customers",
        )
    }
    assert actual == oracle_customers(apps)


def test_orders_are_normalized(warehouse, apps):
    actual = {
        (r[0], r[1]): r[2:]
        for r in rows(
            warehouse,
            "select source_system, order_native_id, customer_native_id, status, currency, "
            "total_amount, shipping_amount, discount_amount, promo_code, placed_at, updated_at "
            "from marts.fct_commerce_orders",
        )
    }
    assert actual == oracle_orders(apps)


def test_order_lines_are_normalized_from_nested_and_separate_endpoints(warehouse, apps):
    actual = {
        (r[0], r[1]): r[2:]
        for r in rows(
            warehouse,
            "select source_system, order_line_native_id, order_native_id, line_number, sku, "
            "quantity, unit_price, line_amount, currency from marts.fct_commerce_order_lines",
        )
    }
    assert actual == oracle_order_lines(apps)


def test_commerce_keys_are_hashes_of_source_and_native_id(warehouse):
    for source, native, order_key, customer_native, customer_key, tenant in rows(
        warehouse,
        "select source_system, order_native_id, order_key, customer_native_id, customer_key, "
        "tenant from marts.fct_commerce_orders",
    ):
        assert order_key == key(source, native)
        assert customer_key == key(source, customer_native)
        assert tenant == TENANT[source]


# --- support ---------------------------------------------------------------------------


def oracle_tickets(apps):
    expected = {}
    for t in apps[8002].state.dataset["tickets"]:
        score = t["satisfaction_score"]
        expected[("ticketdesk", str(t["id"]))] = (
            t["subject"], TICKET_STATUS[("ticketdesk", t["status"])],
            PRIORITY[("ticketdesk", t["priority"])], t["requester"]["email"],
            t["requester"]["name"], None if t["assignee_id"] is None else str(t["assignee_id"]),
            sorted(t["tags"]), round(t["time_spent_hours"] * 60),
            None if score is None or score == 3 else score >= 4,
            iso(t["created_at"]), iso(t["modified_at"]),
        )  # fmt: skip
    for c in apps[8004].state.dataset["cases"]:
        if c["deleted"]:
            continue  # tombstones
        rating = (c["csat"] or {}).get("rating")
        expected[("helpline", c["case_number"])] = (
            c["title"], TICKET_STATUS[("helpline", c["state"])],
            PRIORITY[("helpline", c["urgency"])],
            c["contact"]["email"], None, (c["owner"] or {}).get("staff_id"),
            sorted(c["labels"].split(";")) if c["labels"] else [], c["effort_minutes"],
            None if rating is None else rating == "good",
            iso(c["opened_at"]), iso(c["updated_at"]),
        )  # fmt: skip
    return expected


def oracle_agents(apps):
    expected = {}
    for a in apps[8002].state.dataset["agents"]:
        expected[("ticketdesk", str(a["id"]))] = (
            a["name"], a["email"], a["team"], a["active"], iso(a["modified_at"]),
        )  # fmt: skip
    for s in apps[8004].state.dataset["staff"]:
        expected[("helpline", s["staff_id"])] = (
            s["display_name"], s["email"], s["group"], s["status"] == "active",
            iso(s["updated_at"]),
        )  # fmt: skip
    return expected


def test_tickets_are_normalized(warehouse, apps):
    actual = {
        (r[0], r[1]): (*r[2:8], sorted(r[8] or []), *r[9:])
        for r in rows(
            warehouse,
            "select source_system, ticket_native_id, subject, status, priority, requester_email, "
            "requester_name, assignee_native_id, tags, time_spent_minutes, csat_positive, "
            "created_at, updated_at from marts.fct_support_tickets",
        )
    }
    assert actual == oracle_tickets(apps)


def test_agents_are_normalized(warehouse, apps):
    actual = {
        (r[0], r[1]): r[2:]
        for r in rows(
            warehouse,
            "select source_system, agent_native_id, name, email, team, is_active, updated_at "
            "from marts.dim_support_agents",
        )
    }
    assert actual == oracle_agents(apps)


def test_ticket_assignees_resolve_to_agents(warehouse):
    orphans = rows(
        warehouse,
        "select t.ticket_native_id from marts.fct_support_tickets t "
        "left join marts.dim_support_agents a on a.agent_key = t.assignee_agent_key "
        "where t.assignee_agent_key is not null and a.agent_key is null",
    )
    assert orphans == []


def test_deleted_records_stay_in_staging_with_a_flag(warehouse, apps):
    deleted_orders = sum(o["isDeleted"] for o in apps[8003].state.dataset["orders"])
    tombstones = sum(c["deleted"] for c in apps[8004].state.dataset["cases"])
    assert deleted_orders and tombstones
    assert rows(
        warehouse, "select count(*) from staging.stg_cartwheel__orders where is_deleted"
    ) == [(deleted_orders,)]
    assert rows(warehouse, "select count(*) from staging.stg_helpline__cases where is_deleted") == [
        (tombstones,)
    ]
