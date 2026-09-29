"""The initech_ticketdesk consumer, run through the real contract against the mock API."""

from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import elt_suite.contract as contract
from conftest import MemoryDestination
from elt_suite.config import load_consumer
from elt_suite.contract import SourceError, iter_records
from elt_suite.inference import JobSchema
from elt_suite.load import extract_and_load
from mock_apis.ticketdesk import DEMO_API_KEY, create_app

CONSUMER = "initech_ticketdesk_extract_and_load"


@pytest.fixture
def serve(monkeypatch):
    monkeypatch.setenv("ELT_HOME", str(Path(__file__).parents[1]))
    monkeypatch.setenv("INITECH_TICKETDESK_API_KEY", DEMO_API_KEY)

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


@pytest.mark.parametrize("job", ["tickets", "agents"])
def test_extracts_every_record_by_following_link_headers(serve, consumer, job):
    app = serve()
    assert fetch(consumer, job, datetime(2020, 1, 1)) == app.state.dataset[job]


def test_lower_bound_is_inclusive_despite_exclusive_api_filter(serve, consumer):
    """modified_after is exclusive and timestamps collide, so the consumer must widen it."""
    app = serve()
    tickets = app.state.dataset["tickets"]
    shared = [ts for ts, n in Counter(t["modified_at"] for t in tickets).items() if n > 1]
    bound = min(shared)

    records = fetch(consumer, "tickets", datetime.strptime(bound, "%Y-%m-%d %H:%M:%S"))

    assert records == [t for t in tickets if t["modified_at"] >= bound]
    assert sum(r["modified_at"] == bound for r in records) > 1


# --- valid queries returning no data --------------------------------------------------


def test_lower_bound_after_all_updates_yields_nothing(serve, consumer):
    serve()
    assert fetch(consumer, "agents", datetime(2099, 1, 1)) == []


def test_empty_source_yields_nothing(serve, consumer):
    serve(dataset={"tickets": [], "agents": []})
    assert fetch(consumer, "tickets") == []


# --- invalid queries surface the API's explanation ------------------------------------


def test_bad_api_key_surfaces_the_api_message(serve, consumer, monkeypatch):
    serve()
    monkeypatch.setenv("INITECH_TICKETDESK_API_KEY", "wrong")
    with pytest.raises(SourceError, match="401: The API key is not valid"):
        fetch(consumer, "tickets")


def test_invalid_query_surfaces_every_field_problem(serve, consumer):
    serve()
    bad = consumer.model_copy(update={"extra": {"page_size": 0}})
    with pytest.raises(SourceError, match=r"per_page: must be between 1 and 200; got 0"):
        fetch(bad, "tickets")


# --- end to end -----------------------------------------------------------------------


def test_incremental_rerun_reloads_only_the_boundary(serve, consumer):
    app = serve()
    job = consumer.job("tickets")
    schema = JobSchema.load(CONSUMER, "tickets")
    dest = MemoryDestination()

    first = extract_and_load(consumer, job, schema, dest)
    second = extract_and_load(consumer, job, schema, dest)

    tickets = app.state.dataset["tickets"]
    newest = max(t["modified_at"] for t in tickets)
    assert first.records_loaded == len(tickets)
    assert second.records_loaded == sum(t["modified_at"] == newest for t in tickets)
    assert len(dest.tables[("initech_ticketdesk", "tickets")]) == len(tickets)
