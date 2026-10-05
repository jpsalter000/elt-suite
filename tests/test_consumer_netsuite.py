"""The vandelay_netsuite consumer, run through the real contract against the NetSuite mock."""

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import consumers.netsuite.client as client
import elt_suite.contract as contract
from conftest import MemoryDestination
from elt_suite.config import load_consumer
from elt_suite.contract import SourceError, iter_records
from elt_suite.inference.executor import infer_schema
from elt_suite.load import extract_and_load
from mock_apis.netsuite import DEMO_CREDENTIALS, create_app

CONSUMER = "vandelay_netsuite_extract_and_load"
ENV = {
    "VANDELAY_NS_ACCOUNT_ID": DEMO_CREDENTIALS["account_id"],
    "VANDELAY_NS_CONSUMER_KEY": DEMO_CREDENTIALS["consumer_key"],
    "VANDELAY_NS_CONSUMER_SECRET": DEMO_CREDENTIALS["consumer_secret"],
    "VANDELAY_NS_TOKEN_ID": DEMO_CREDENTIALS["token_id"],
    "VANDELAY_NS_TOKEN_SECRET": DEMO_CREDENTIALS["token_secret"],
}
TABLES = {
    "departments": "department",
    "employees": "employee",
    "customers": "customer",
    "projects": "job",
    "items": "item",
    "time_entries": "timebill",
    "transactions": "transaction",
    "transaction_lines": "transactionline",
    "transaction_accounting_lines": "transactionaccountingline",
    "accounts": "account",
}
EARLY = datetime(2015, 1, 1)


@pytest.fixture
def serve(monkeypatch):
    monkeypatch.setenv("ELT_HOME", str(Path(__file__).parents[1]))
    monkeypatch.delenv("VANDELAY_NS_BASE_URL", raising=False)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    requested: list[str] = []

    def _serve(**app_kwargs):
        app = create_app(**app_kwargs)

        def make():
            http = TestClient(app)
            http.event_hooks = {"request": [lambda r: requested.append(str(r.url))]}
            return http

        monkeypatch.setattr(contract, "make_client", make)
        app.state.requested = requested
        return app

    return _serve


@pytest.fixture
def consumer():
    return load_consumer(CONSUMER)


def fetch(consumer, job, lower_bound=None):
    return list(iter_records(consumer, consumer.job(job), lower_bound))


# --- valid queries returning data -----------------------------------------------------


def test_the_consumer_configures_every_reporting_job(consumer):
    assert {job.name for job in consumer.jobs} == set(TABLES)
    full_refresh = {job.name for job in consumer.jobs if job.incremental_loading is None}
    assert full_refresh == {
        "departments", "items", "transaction_lines", "transaction_accounting_lines", "accounts",
    }  # fmt: skip


@pytest.mark.parametrize(("job", "table"), TABLES.items())
def test_every_job_extracts_every_record(serve, consumer, job, table):
    app = serve()
    records = fetch(consumer, job, EARLY)
    assert len(records) == len(app.state.dataset[table])
    assert all("links" not in r for r in records)
    pks = consumer.job(job).primary_keys
    assert len({tuple(r[k] for k in pks) for r in records}) == len(records)


def test_dates_and_datetimes_come_back_in_iso_form(serve, consumer):
    serve()
    employees = fetch(consumer, "employees", EARLY)
    for e in employees:
        datetime.strptime(e["hiredate"], "%Y-%m-%d")
        datetime.strptime(e["lastmodifieddate"], "%Y-%m-%d %H:%M:%S")
    entries = fetch(consumer, "time_entries", EARLY)
    assert all(datetime.strptime(t["trandate"], "%Y-%m-%d") for t in entries)


def test_large_tables_are_paged(serve, consumer):
    app = serve()
    records = fetch(consumer, "time_entries", EARLY)
    pages = [url for url in app.state.requested if "suiteql" in url]
    assert len(pages) == -(-len(records) // consumer.extra["page_size"])
    assert {int(r["id"]) for r in records} == {t["id"] for t in app.state.dataset["timebill"]}


def test_lower_bound_is_inclusive(serve, consumer):
    app = serve()
    stamps = sorted(t["lastmodifieddate"] for t in app.state.dataset["timebill"])
    bound = stamps[len(stamps) * 3 // 4].replace(tzinfo=None)
    records = fetch(consumer, "time_entries", bound)
    expected = {t["id"] for t in app.state.dataset["timebill"]
                if t["lastmodifieddate"].replace(tzinfo=None) >= bound}  # fmt: skip
    assert {int(r["id"]) for r in records} == expected


def test_incremental_rerun_resumes_from_the_watermark(serve, consumer):
    serve()
    job = consumer.job("time_entries")
    records = fetch(consumer, "time_entries", EARLY)
    schema = infer_schema(consumer, job, records)
    destination = MemoryDestination()
    first = extract_and_load(consumer, job, schema, destination)
    second = extract_and_load(consumer, job, schema, destination)
    assert first.records_loaded == len(records)
    assert 0 < second.records_loaded < 10  # only rows at the saved watermark
    assert second.max_incremental_value == first.max_incremental_value


def test_base_url_env_points_the_consumer_at_another_host(serve, consumer, monkeypatch):
    app = serve()
    monkeypatch.setenv("VANDELAY_NS_BASE_URL", "http://mock-netsuite:8005")
    assert fetch(consumer, "departments")
    assert all(url.startswith("http://mock-netsuite:8005/") for url in app.state.requested)


# --- valid queries returning no data --------------------------------------------------


def test_a_lower_bound_after_every_change_returns_nothing(serve, consumer):
    serve()
    assert fetch(consumer, "time_entries", datetime(2030, 1, 1)) == []


# --- invalid queries surface the API's explanation ------------------------------------


def test_bad_credentials_surface_netsuites_login_error(serve, consumer, monkeypatch):
    serve()
    monkeypatch.setenv("VANDELAY_NS_TOKEN_SECRET", "wrong")
    with pytest.raises(SourceError, match=r"401.*Invalid login attempt.*signature"):
        fetch(consumer, "departments")


def test_rejected_queries_surface_netsuites_explanation(serve, consumer, monkeypatch):
    serve()
    monkeypatch.setattr(client, "DEPARTMENT_COLUMNS", "id, budget")
    with pytest.raises(SourceError, match="Field 'budget' for record 'department' was not found"):
        fetch(consumer, "departments")


def test_an_oversized_page_size_surfaces_the_limit(serve, consumer):
    serve()
    oversized = consumer.model_copy(update={"extra": {"page_size": 5000}})
    with pytest.raises(SourceError, match="between 1 and 1000"):
        fetch(oversized, "departments")
