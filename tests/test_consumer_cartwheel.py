"""The umbrella_cartwheel consumer, run through the real contract against the mock API."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import elt_suite.contract as contract
from conftest import MemoryDestination
from elt_suite.config import load_consumer
from elt_suite.contract import SourceError, iter_records
from elt_suite.inference import JobSchema
from elt_suite.load import extract_and_load
from mock_apis.cartwheel import DEMO_PASSWORD, DEMO_USERNAME, create_app

CONSUMER = "umbrella_cartwheel_extract_and_load"
PKS = {"customers": "customerId", "orders": "orderId", "order_items": "orderItemId"}


@pytest.fixture
def serve(monkeypatch):
    monkeypatch.setenv("ELT_HOME", str(Path(__file__).parents[1]))
    monkeypatch.setenv("UMBRELLA_CARTWHEEL_USERNAME", DEMO_USERNAME)
    monkeypatch.setenv("UMBRELLA_CARTWHEEL_PASSWORD", DEMO_PASSWORD)

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


@pytest.mark.parametrize("job", ["customers", "orders", "order_items"])
def test_extracts_every_record_once_including_soft_deletes(serve, consumer, job):
    """Uses the stable sort, so the default sort's duplicates and skips never happen."""
    app = serve()
    records = fetch(consumer, job, datetime(2020, 1, 1, tzinfo=UTC))
    pk = PKS[job]
    assert [r[pk] for r in records] == sorted(r[pk] for r in app.state.dataset[job])


def test_soft_deleted_records_are_extracted_so_deletes_propagate(serve, consumer):
    app = serve()
    records = fetch(consumer, "customers", datetime(2020, 1, 1, tzinfo=UTC))
    deleted = {c["customerId"] for c in app.state.dataset["customers"] if c["isDeleted"]}
    assert deleted and deleted <= {r["customerId"] for r in records if r["isDeleted"]}


def test_lower_bound_is_sent_as_inclusive_unix_seconds(serve, consumer):
    app = serve()
    bound = datetime(2026, 6, 1, tzinfo=UTC)
    records = fetch(consumer, "orders", bound)
    since = int(bound.timestamp())
    assert {r["orderId"] for r in records} == {
        o["orderId"] for o in app.state.dataset["orders"] if o["modified"] >= since
    }


# --- valid queries returning no data --------------------------------------------------


def test_lower_bound_after_all_updates_yields_nothing(serve, consumer):
    serve()
    assert fetch(consumer, "order_items", datetime(2099, 1, 1, tzinfo=UTC)) == []


def test_empty_source_yields_nothing(serve, consumer):
    serve(dataset={"customers": [], "orders": [], "order_items": []})
    assert fetch(consumer, "customers") == []


# --- invalid queries surface the API's explanation ------------------------------------


def test_bad_password_surfaces_the_problem_detail(serve, consumer, monkeypatch):
    serve()
    monkeypatch.setenv("UMBRELLA_CARTWHEEL_PASSWORD", "wrong")
    with pytest.raises(SourceError, match="401 Unauthorized: username or password is incorrect"):
        fetch(consumer, "orders")


def test_invalid_query_surfaces_every_parameter(serve, consumer):
    serve()
    bad = consumer.model_copy(update={"extra": {"page_size": 500}})
    with pytest.raises(SourceError, match=r"limit: must be between 1 and 250; got 500"):
        fetch(bad, "orders")


# --- end to end -----------------------------------------------------------------------


def test_incremental_rerun_resumes_from_the_epoch_watermark(serve, consumer):
    app = serve()
    job = consumer.job("orders")
    dest = MemoryDestination()

    first = extract_and_load(consumer, job, JobSchema.load(CONSUMER, "orders"), dest)
    second = extract_and_load(consumer, job, JobSchema.load(CONSUMER, "orders"), dest)

    orders = app.state.dataset["orders"]
    newest = max(o["modified"] for o in orders)
    assert first.records_loaded == len(orders)
    assert first.max_incremental_value == str(newest)
    assert second.records_loaded == sum(o["modified"] == newest for o in orders)
