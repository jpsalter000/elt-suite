"""The globex_shopfront consumer, run through the real contract against the mock API."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import elt_suite.contract as contract
from conftest import MemoryDestination
from elt_suite.config import load_consumer
from elt_suite.contract import SourceError, iter_records
from elt_suite.inference import JobSchema
from elt_suite.load import extract_and_load
from mock_apis.shopfront import DEMO_CLIENT_ID, DEMO_CLIENT_SECRET, create_app

CONSUMER = "globex_shopfront_extract_and_load"


class SteppingClock:
    """Advances a fixed step on every read, so tokens expire mid-extraction."""

    def __init__(self, step: timedelta) -> None:
        self.now, self.step = datetime(2026, 9, 29, tzinfo=UTC), step

    def __call__(self) -> datetime:
        self.now += self.step
        return self.now


@pytest.fixture
def serve(monkeypatch):
    """Route the consumer's HTTP client to an in-process Shopfront app."""
    monkeypatch.setenv("ELT_HOME", str(Path(__file__).parents[1]))
    monkeypatch.setenv("GLOBEX_SHOPFRONT_CLIENT_ID", DEMO_CLIENT_ID)
    monkeypatch.setenv("GLOBEX_SHOPFRONT_CLIENT_SECRET", DEMO_CLIENT_SECRET)

    def _serve(**app_kwargs):
        app = create_app(**app_kwargs)
        monkeypatch.setattr(contract, "make_client", lambda: TestClient(app))
        return app

    return _serve


@pytest.fixture
def consumer():
    return load_consumer(CONSUMER)


def fetch(consumer, job, lower_bound=None):
    return list(iter_records(consumer, consumer.job(job), lower_bound))


# --- valid queries returning data -----------------------------------------------------


@pytest.mark.parametrize("job", ["customers", "orders"])
def test_extracts_every_record(serve, consumer, job):
    app = serve()
    records = fetch(consumer, job, datetime(2020, 1, 1, tzinfo=UTC))
    assert records == app.state.dataset[job]


def test_configured_lower_bound_filters_on_updated_at(serve, consumer):
    app = serve()
    bound = datetime(2026, 6, 1, tzinfo=UTC)
    records = fetch(consumer, "orders", bound)
    expected = [o for o in app.state.dataset["orders"] if o["updated_at"] >= "2026-06-01T00:00:00Z"]
    assert records == expected and 0 < len(records) < len(app.state.dataset["orders"])


def test_naive_lower_bound_is_treated_as_utc(serve, consumer):
    serve()
    aware = fetch(consumer, "customers", datetime(2026, 6, 1, tzinfo=UTC))
    naive = fetch(consumer, "customers", datetime(2026, 6, 1))
    assert aware == naive


def test_refreshes_expired_token_mid_pagination(serve, consumer):
    app = serve(clock=SteppingClock(timedelta(seconds=40)), token_ttl_seconds=100)
    records = fetch(consumer, "orders", datetime(2020, 1, 1, tzinfo=UTC))
    assert records == app.state.dataset["orders"]
    assert app.state.tokens.refreshes >= 2  # 540 orders / 100 per page, token lives ~2 pages


def test_reauthenticates_when_refresh_token_is_rejected(serve, consumer):
    clock = SteppingClock(timedelta(seconds=40))
    app = serve(clock=clock, token_ttl_seconds=100)
    original = app.state.tokens.issue

    def issue_then_forget_refresh_tokens():
        pair = original()
        app.state.tokens.refresh.clear()  # e.g. the provider revoked them
        return pair

    app.state.tokens.issue = issue_then_forget_refresh_tokens
    records = fetch(consumer, "customers", datetime(2020, 1, 1, tzinfo=UTC))
    assert records == app.state.dataset["customers"]


# --- valid queries returning no data --------------------------------------------------


def test_lower_bound_after_all_updates_yields_nothing(serve, consumer):
    serve()
    assert fetch(consumer, "orders", datetime(2099, 1, 1, tzinfo=UTC)) == []


def test_empty_source_yields_nothing(serve, consumer):
    serve(dataset={"customers": [], "orders": []})
    assert fetch(consumer, "customers") == []


# --- invalid queries surface the API's explanation ------------------------------------


def test_bad_credentials_surface_the_api_message(serve, consumer, monkeypatch):
    serve()
    monkeypatch.setenv("GLOBEX_SHOPFRONT_CLIENT_SECRET", "wrong")
    with pytest.raises(SourceError, match="401 invalid_client: client_id or client_secret"):
        fetch(consumer, "customers")


def test_invalid_query_surfaces_every_parameter_problem(serve, consumer):
    serve()
    bad = consumer.model_copy(update={"extra": {"page_size": 500}})
    with pytest.raises(SourceError, match=r"limit: must be between 1 and 100; got 500"):
        fetch(bad, "customers")


# --- end to end -----------------------------------------------------------------------


def test_incremental_rerun_only_loads_changed_records(serve, consumer):
    app = serve()
    job = consumer.job("orders")
    schema = JobSchema.load(CONSUMER, "orders")
    dest = MemoryDestination()

    first = extract_and_load(consumer, job, schema, dest)
    assert first.records_loaded == len(app.state.dataset["orders"])

    second = extract_and_load(consumer, job, schema, dest)
    newest = max(o["updated_at"] for o in app.state.dataset["orders"])
    at_watermark = [o for o in app.state.dataset["orders"] if o["updated_at"] == newest]
    assert second.records_loaded == len(at_watermark)  # inclusive bound re-reads the boundary
    assert len(dest.tables[("globex_shopfront", "orders")]) == len(app.state.dataset["orders"])
