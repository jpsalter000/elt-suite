"""End to end: mock NetSuite -> extract-and-load -> dbt -> reporting views, checked by an oracle.

The oracle is a separate implementation of the modernized utilization and margin
rules in plain Python, computed straight from the mock dataset and the dbt seeds.
If the SQL and the oracle agree on every employee-week and every project, the
views implement the modernized rules.
"""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import elt_suite.contract as contract
from elt_suite.pipeline import load_pipeline, run_pipeline
from mock_apis.netsuite import DEMO_CREDENTIALS, create_app
from mock_apis.netsuite.data import generate

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("WAREHOUSE_DSN"), reason="WAREHOUSE_DSN not set"),
]

ROOT = Path(__file__).parents[1]
SEEDS = ROOT / "transform" / "seeds"
CALENDAR_START = date(2026, 1, 1)
SCHEMAS = ["vandelay_netsuite", "reference", "staging", "intermediate", "marts", "reporting"]
CENT, BASIS = Decimal("0.01"), Decimal("0.0001")
REVENUE_DOCS = {"SalesOrd", "CustInvc", "CustCred"}
PENDING_PURCHASE_ORDER = {"Pending Supervisor Approval", "Pending Billing"}


def _seed(name: str) -> list[dict[str, str]]:
    with (SEEDS / f"{name}.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _d(value) -> Decimal:
    return Decimal(str(value))


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if not denominator:
        return None
    return (numerator / denominator).quantize(BASIS, rounding=ROUND_HALF_UP)


def _week_start(day: date) -> date:
    return day - timedelta(days=(day.weekday() + 1) % 7)  # Sunday-based weeks


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    """Run utilization_daily against the in-process mock; yield a connection."""
    import psycopg

    mp = pytest.MonkeyPatch()
    mp.setenv("ELT_HOME", str(ROOT))
    mp.delenv("VANDELAY_NS_BASE_URL", raising=False)
    for key, value in DEMO_CREDENTIALS.items():
        mp.setenv(f"VANDELAY_NS_{key.upper()}", value)
    scratch = tmp_path_factory.mktemp("dbt")
    mp.setenv("DBT_TARGET_PATH", str(scratch / "target"))
    mp.setenv("DBT_LOG_PATH", str(scratch / "logs"))
    app = create_app()
    mp.setattr(contract, "make_client", lambda: TestClient(app))

    dsn = os.environ["WAREHOUSE_DSN"]
    with psycopg.connect(dsn, autocommit=True) as conn:
        for schema in SCHEMAS:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")

    results = run_pipeline(load_pipeline("utilization_daily"))
    failed = {r.task: r.error for r in results if r.status != "succeeded"}
    assert not failed, failed
    with psycopg.connect(dsn) as conn:
        yield conn
    mp.undo()


@pytest.fixture(scope="module")
def data():
    return generate()


# --- the oracle ------------------------------------------------------------------------


def oracle_weekly_utilization(data) -> dict[tuple[int, date], dict[str, Decimal | None]]:
    categories = {row["item_name"].lower(): row for row in _seed("time_categories")}
    items = {i["id"]: i["itemid"] for i in data["item"]}
    end = max(t["trandate"] for t in data["timebill"])

    exempt_by_day: dict[tuple[int, date], Decimal] = defaultdict(Decimal)
    weeks: dict[tuple[int, date], dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for t in data["timebill"]:
        category = categories.get(items[t["item"]].lower())
        code = category["category_code"] if category else "unmapped"
        hours = _d(t["hours"])
        bucket = weeks[(t["employee"], _week_start(t["trandate"]))]
        if category and category["counts_as_worked"] == "true":
            bucket["worked_hours"] += hours
        if code in ("project_work", "client_bd", "internal_productive", "internal_admin"):
            bucket[f"{code}_hours"] += hours
        if code == "exempt":
            bucket["exempt_hours"] += hours
            exempt_by_day[(t["employee"], t["trandate"])] += hours

    for e in data["employee"]:
        first = max(e["hiredate"], CALENDAR_START)
        last = min(e["releasedate"] or end, end)
        standard = _d(e["custentity_hours_per_day"])
        day = first
        while day <= last:
            bucket = weeks[(e["id"], _week_start(day))]
            if day.weekday() < 5:
                bucket["standard_hours"] += standard
                bucket["expected_hours"] += max(
                    standard - exempt_by_day[(e["id"], day)], Decimal(0)
                )
            else:
                bucket["standard_hours"] += 0
            day += timedelta(days=1)

    report = {}
    for key, b in weeks.items():
        expected = b["expected_hours"]
        report[key] = {
            "expected_hours": expected,
            "worked_hours": b["worked_hours"],
            "exempt_hours": b["exempt_hours"],
            "net_utilization_pct": _pct(b["worked_hours"], expected),
            "project_work_pct": _pct(b["project_work_hours"], expected),
            "client_bd_pct": _pct(b["client_bd_hours"], expected),
            "internal_pct": _pct(
                b["internal_productive_hours"] + b["internal_admin_hours"], expected
            ),
            "exempt_pct": _pct(b["exempt_hours"], b["standard_hours"]),
        }
    return report


def oracle_project_margin(data) -> dict[int, dict[str, Decimal | None]]:
    classes = {row["account_number"]: row["account_class"] for row in _seed("account_classes")}
    accounts = {a["id"]: a for a in data["account"]}
    transactions = {t["id"]: t for t in data["transaction"]}
    projects = {p["id"]: p for p in data["job"]}
    lines = {(ln["transaction"], ln["id"]): ln for ln in data["transactionline"]}
    employees = {e["id"]: e for e in data["employee"]}

    totals: dict[int, dict[str, Decimal]] = {p: defaultdict(Decimal) for p in projects}
    for acct_line in data["transactionaccountingline"]:
        line = lines[(acct_line["transaction"], acct_line["transactionline"])]
        if line["mainline"] or line["entity"] not in projects:
            continue
        tx = transactions[line["transaction"]]
        account = accounts[acct_line["account"]]
        project = projects[line["entity"]]
        kind, status = tx["type"], tx["status"]
        amount = _d(acct_line["amount"])
        if account["accttype"] in ("Income", "DeferRevenue"):
            amount = -amount
        account_class = classes.get(account.get("acctnumber") or "")
        deferred = account["accttype"] == "DeferRevenue"
        excluded = (
            account_class == "excluded"
            or status == "Rejected by Supervisor"
            or (kind == "PurchOrd" and status not in PENDING_PURCHASE_ORDER)
            or (deferred and kind not in REVENUE_DOCS)
            or kind == "VendAuth"
        )  # fmt: skip
        if excluded:
            continue
        complete = project["custentity_project_status"][0] in "45"
        counts = kind in ("CustInvc", "CustCred") if complete else kind == "SalesOrd"
        revenue_like = deferred or account_class in (
            "revenue", "pass_through_travel", "pass_through_incentive",
        )  # fmt: skip
        t = totals[project["id"]]
        if kind in REVENUE_DOCS and revenue_like and counts:
            t["price"] += amount
            if account_class and account_class.startswith("pass_through"):
                t["pass_through_revenue"] += amount
        for cost in ("field_cost", "incentive_cost", "travel_cost"):
            if account_class == cost:
                t[cost] += amount

    for entry in data["timebill"]:
        if entry["customer"] in totals:
            t = totals[entry["customer"]]
            employee = employees[entry["employee"]]
            hours = _d(entry["hours"])
            t["total_hours"] += hours
            t["time_cost"] += hours * _d(employee["laborcost"])
            t["burdened_time_cost"] += hours * _d(employee["custentity_burdened_cost"])

    report = {}
    for project_id, t in totals.items():
        direct = t["field_cost"] + t["incentive_cost"] + t["travel_cost"]
        net_revenue = t["price"] - t["pass_through_revenue"]
        profit = t["price"] - direct - t["time_cost"]
        margin = t["price"] - direct - t["burdened_time_cost"]
        report[project_id] = {
            "total_hours": t["total_hours"],
            "time_cost": t["time_cost"].quantize(CENT, rounding=ROUND_HALF_UP),
            "price": t["price"],
            "net_revenue": net_revenue,
            "field_cost": t["field_cost"],
            "incentive_cost": t["incentive_cost"],
            "travel_cost": t["travel_cost"],
            "project_profit": profit.quantize(CENT, rounding=ROUND_HALF_UP),
            "project_profit_pct": _pct(profit, net_revenue),
            "contribution_margin": margin.quantize(CENT, rounding=ROUND_HALF_UP),
            "contribution_margin_pct": _pct(margin, net_revenue),
        }
    return report


# --- the comparison --------------------------------------------------------------------


def _rows(conn, sql):
    cur = conn.execute(sql)
    names = [c.name for c in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def test_weekly_utilization_matches_the_oracle(warehouse, data):
    expected = oracle_weekly_utilization(data)
    rows = _rows(warehouse, "SELECT * FROM reporting.rpt_utilization_weekly")
    actual = {
        (r["employee_id"], r["week_start"]): {k: r[k] for k in next(iter(expected.values()))}
        for r in rows
    }
    assert actual.keys() == expected.keys()
    mismatches = {k: (actual[k], expected[k]) for k in expected if actual[k] != expected[k]}
    assert not mismatches, list(mismatches.items())[:3]


def test_project_margin_matches_the_oracle(warehouse, data):
    expected = oracle_project_margin(data)
    rows = _rows(warehouse, "SELECT * FROM reporting.rpt_project_margin")
    actual = {r["project_id"]: {k: r[k] for k in next(iter(expected.values()))} for r in rows}
    assert actual.keys() == expected.keys()
    mismatches = {k: (actual[k], expected[k]) for k in expected if actual[k] != expected[k]}
    assert not mismatches, list(mismatches.items())[:3]


def test_the_oracle_exercises_the_interesting_cases(data):
    weeks = oracle_weekly_utilization(data)
    assert any(w["net_utilization_pct"] and w["net_utilization_pct"] > 1 for w in weeks.values())
    margins = oracle_project_margin(data)
    assert any(m["project_profit_pct"] is None for m in margins.values()), "no revenue yet"
    assert any(m["project_profit"] < 0 for m in margins.values()), "a loss-making project"


def test_reporting_hides_individual_cost_rates(warehouse):
    columns = {
        r["column_name"]
        for r in _rows(
            warehouse,
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'reporting'",
        )
    }
    assert not {"cost_per_hour", "burdened_cost_per_hour"} & columns
    per_entry = _rows(
        warehouse,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'reporting' AND table_name = 'fct_time_entries'",
    )
    assert not {"time_cost", "burdened_time_cost"} & {r["column_name"] for r in per_entry}


def test_power_bi_can_read_reporting_but_not_the_raw_data(warehouse):
    granted = _rows(
        warehouse,
        "SELECT has_schema_privilege('reporting_reader', 'reporting', 'USAGE') AS reporting, "
        "has_schema_privilege('reporting_reader', 'vandelay_netsuite', 'USAGE') AS raw, "
        "pg_has_role('powerbi', 'reporting_reader', 'MEMBER') AS member",
    )
    assert granted == [{"reporting": True, "raw": False, "member": True}]
