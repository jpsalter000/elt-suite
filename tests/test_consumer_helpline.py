"""The hooli_helpline consumer, run through the real contract against the mock API."""

import copy
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
from mock_apis.helpline import DEMO_KEY_ID, DEMO_SECRET, create_app
from mock_apis.helpline.data import generate

CONSUMER = "hooli_helpline_extract_and_load"


@pytest.fixture
def serve(monkeypatch):
    monkeypatch.setenv("ELT_HOME", str(Path(__file__).parents[1]))
    monkeypatch.setenv("HOOLI_HELPLINE_KEY_ID", DEMO_KEY_ID)
    monkeypatch.setenv("HOOLI_HELPLINE_SECRET", DEMO_SECRET)

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


@pytest.mark.parametrize(("job", "ident"), [("cases", "case_number"), ("staff", "staff_id")])
def test_extracts_every_change_by_following_tokens(serve, consumer, job, ident):
    app = serve()
    records = fetch(consumer, job, datetime(2020, 1, 1, tzinfo=UTC))
    assert sorted(r[ident] for r in records) == sorted(r[ident] for r in app.state.dataset[job])


def test_deleted_cases_arrive_as_tombstones(serve, consumer):
    serve()
    records = fetch(consumer, "cases", datetime(2020, 1, 1, tzinfo=UTC))
    tombstones = [r for r in records if r["deleted"]]
    assert tombstones and all(
        set(t) == {"case_number", "deleted", "updated_at"} for t in tombstones
    )


def test_lower_bound_is_inclusive_and_offset_aware(serve, consumer):
    app = serve()
    bound = datetime(2026, 6, 1, tzinfo=UTC)
    records = fetch(consumer, "cases", bound)
    assert {r["case_number"] for r in records} == {
        c["case_number"]
        for c in app.state.dataset["cases"]
        if datetime.fromisoformat(c["updated_at"]) >= bound
    }


# --- late arrivals --------------------------------------------------------------------


def _case(template, number, updated_at, committed_at):
    case = copy.deepcopy(template)
    case.update(
        case_number=number,
        deleted=False,
        updated_at=updated_at.isoformat(timespec="seconds"),
        _committed_at=committed_at,
    )
    return case


@pytest.fixture
def late_arrival():
    """A: stamped 09:59, committed 10:00. R: stamped 09:58 but committed 10:20."""
    template = next(c for c in generate()["cases"] if not c["deleted"])
    t = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    early = _case(template, "HL-A", t - timedelta(minutes=1), t)
    late = _case(template, "HL-R", t - timedelta(minutes=2), t + timedelta(minutes=20))
    return {"cases": [early, late], "staff": []}, t + timedelta(minutes=5)


@pytest.mark.parametrize(("lookback", "loaded"), [(0, {"HL-A"}), (1800, {"HL-A", "HL-R"})])
def test_lookback_catches_changes_committed_after_the_last_run(
    serve, consumer, late_arrival, lookback, loaded
):
    dataset, first_run_at = late_arrival
    app = serve(dataset=dataset)
    job = consumer.job("cases")
    job = job.model_copy(
        update={
            "incremental_loading": job.incremental_loading.model_copy(
                update={"lookback_seconds": lookback}
            )
        }
    )
    schema = JobSchema.load(CONSUMER, "cases")
    dest = MemoryDestination()

    app.state.visible_until = first_run_at  # R is still uncommitted
    extract_and_load(consumer, job, schema, dest)
    app.state.visible_until = None  # R commits, stamped before the saved watermark
    extract_and_load(consumer, job, schema, dest)

    assert {k[0] for k in dest.tables[("hooli_helpline", "cases")]} == loaded


def test_configured_lookback_is_thirty_minutes(consumer):
    assert consumer.job("cases").incremental_loading.lookback_seconds == 1800


# --- valid queries returning no data --------------------------------------------------


def test_lower_bound_after_all_updates_yields_nothing(serve, consumer):
    serve()
    assert fetch(consumer, "staff", datetime(2099, 1, 1, tzinfo=UTC)) == []


def test_empty_source_yields_nothing(serve, consumer):
    serve(dataset={"cases": [], "staff": []})
    assert fetch(consumer, "cases") == []


# --- invalid queries surface the API's explanation ------------------------------------


def test_bad_secret_surfaces_the_signature_error(serve, consumer, monkeypatch):
    serve()
    monkeypatch.setenv("HOOLI_HELPLINE_SECRET", "wrong")
    with pytest.raises(SourceError, match="401 invalid_signature: signature does not match"):
        fetch(consumer, "cases")


def test_invalid_query_surfaces_every_problem(serve, consumer):
    serve()
    bad = consumer.model_copy(update={"extra": {"page_size": 0}})
    with pytest.raises(SourceError, match=r"limit: must be between 1 and 500; got 0"):
        fetch(bad, "staff")
