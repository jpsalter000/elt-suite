import os
from dataclasses import replace
from datetime import datetime
from uuid import UUID

import pytest

from elt_suite.inference import InferenceError, JobSchema, run_inference
from elt_suite.load import LoadError, extract_and_load, resolve_lower_bound, run_full
from elt_suite.load.ddl import RUN_ID_COLUMN, table_columns


class MemoryDestination:
    """In-memory Destination used to test the executor without a database."""

    def __init__(self):
        self.tables: dict[tuple[str, str], dict[tuple, dict]] = {}
        self.runs: dict[UUID, list] = {}  # run_id -> every recorded state of the run

    def ensure_table(self, schema, table, columns, primary_keys):
        self.tables.setdefault((schema, table), {})

    def upsert(self, schema, table, columns, primary_keys, rows):
        target = self.tables[(schema, table)]
        for row in rows:
            target[tuple(row[k] for k in primary_keys)] = {c: row.get(c) for c in columns}

    def record_run(self, schema, run):
        self.runs.setdefault(run.run_id, []).append(replace(run))
        self.last_run = replace(run)

    def last_watermark(self, schema, consumer, job):
        finished = [
            states[-1]
            for states in self.runs.values()
            if states[-1].job == job
            and states[-1].status == "succeeded"
            and states[-1].max_incremental_value
        ]
        return (
            max(finished, key=lambda r: r.finished_at).max_incremental_value if finished else None
        )

    def close(self):
        pass


RECORDS = [
    {"id": 1, "name": "a", "meta": {"k": 1}, "updated_at": "2024-02-01 00:00:00"},
    {"id": 2, "name": "b", "meta": {"k": 2}, "updated_at": "2024-03-01 12:00:00"},
    {"id": 1, "name": "a2", "meta": {"k": 3}, "updated_at": "2024-02-15 00:00:00"},
]


@pytest.fixture
def inferred(fake_consumer, fake_client):
    fake_client.extend(RECORDS)
    job = fake_consumer.jobs[0]
    run_inference(fake_consumer, job)
    return fake_consumer, job, JobSchema.load(fake_consumer.name, job.name)


def test_ddl_columns(inferred):
    _, _, schema = inferred
    cols = {c.name: c for c in table_columns(schema)}
    assert cols["id"].pg_type == "BIGINT" and not cols["id"].nullable
    assert cols["meta"].pg_type == "JSONB"
    assert cols["updated_at"].pg_type == "TIMESTAMPTZ"
    assert cols[RUN_ID_COLUMN].pg_type == "UUID"


def test_load_stamps_run_id_and_records_run(inferred):
    consumer, job, schema = inferred
    dest = MemoryDestination()
    result = extract_and_load(consumer, job, schema, dest)

    rows = dest.tables[("demo_fake", "widgets")]
    assert len(rows) == 2  # id=1 upserted, last write wins
    assert rows[(1,)]["name"] == "a2"
    assert {r[RUN_ID_COLUMN] for r in rows.values()} == {result.run_id}
    assert isinstance(rows[(1,)]["updated_at"], datetime)

    assert [(r.status, r.records_loaded) for r in dest.runs[result.run_id]] == [
        ("running", None),
        ("succeeded", 3),
    ]
    assert dest.last_run.lower_bound == "2024-01-01 00:00:00"
    assert result.max_incremental_value == "2024-03-01 12:00:00"


def test_load_fails_on_unknown_field_and_records_failure(inferred, fake_client):
    consumer, job, schema = inferred
    fake_client.append({"id": 3, "surprise": 1, "updated_at": "2024-04-01 00:00:00"})
    dest = MemoryDestination()
    with pytest.raises(LoadError, match=r"not in the inferred schema: \['surprise'\]"):
        extract_and_load(consumer, job, schema, dest)
    assert dest.last_run.status == "failed"
    assert "surprise" in dest.last_run.error


def test_run_full_requires_schema(fake_consumer):
    with pytest.raises(InferenceError, match="elt infer"):
        run_full(fake_consumer, fake_consumer.jobs[0])


# --- state-backed incremental loading -------------------------------------------------


def test_next_run_resumes_from_last_successful_watermark(inferred, fake_client):
    consumer, job, schema = inferred
    dest = MemoryDestination()
    first = extract_and_load(consumer, job, schema, dest)

    fake_client.append({"id": 3, "name": "c", "meta": {}, "updated_at": "2024-05-01 00:00:00"})
    second = extract_and_load(consumer, job, schema, dest)

    # Inclusive bound: the boundary record (id=2) is re-read, older ones are not.
    assert dest.last_run.lower_bound == first.max_incremental_value == "2024-03-01 12:00:00"
    assert second.records_loaded == 2
    assert second.max_incremental_value == "2024-05-01 00:00:00"
    rows = dest.tables[("demo_fake", "widgets")]
    assert rows[(1,)][RUN_ID_COLUMN] == first.run_id
    assert rows[(2,)][RUN_ID_COLUMN] == rows[(3,)][RUN_ID_COLUMN] == second.run_id


def test_failed_runs_do_not_advance_state(inferred, fake_client):
    consumer, job, schema = inferred
    dest = MemoryDestination()
    first = extract_and_load(consumer, job, schema, dest)

    fake_client.append({"id": 9, "surprise": 1, "updated_at": "2025-01-01 00:00:00"})
    with pytest.raises(LoadError):
        extract_and_load(consumer, job, schema, dest)

    assert dest.last_watermark("demo_fake", consumer.name, job.name) == "2024-03-01 12:00:00"
    assert resolve_lower_bound(consumer, job, dest) == datetime(2024, 3, 1, 12)
    assert first.max_incremental_value == "2024-03-01 12:00:00"


def test_full_refresh_ignores_state(inferred):
    consumer, job, schema = inferred
    dest = MemoryDestination()
    extract_and_load(consumer, job, schema, dest)
    result = extract_and_load(consumer, job, schema, dest, full_refresh=True)
    assert result.records_loaded == 3
    assert dest.last_run.lower_bound == "2024-01-01 00:00:00"


def test_configured_lower_bound_wins_when_later_than_state(inferred):
    consumer, job, _ = inferred
    dest = MemoryDestination()
    dest.last_watermark = lambda *_: "2023-06-01 00:00:00"
    assert resolve_lower_bound(consumer, job, dest) == datetime(2024, 1, 1)


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("WAREHOUSE_DSN"), reason="WAREHOUSE_DSN not set")
def test_postgres_end_to_end(inferred, fake_client):
    import psycopg

    consumer, job, _ = inferred
    with psycopg.connect(os.environ["WAREHOUSE_DSN"], autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS demo_fake CASCADE")

    first = run_full(consumer, job)
    fake_client.append({"id": 3, "name": "c", "meta": {}, "updated_at": "2024-05-01 00:00:00"})
    second = run_full(consumer, job)
    third = run_full(consumer, job, full_refresh=True)

    with psycopg.connect(os.environ["WAREHOUSE_DSN"]) as conn:
        rows = conn.execute("SELECT id, name FROM demo_fake.widgets ORDER BY id").fetchall()
        runs = conn.execute(
            "SELECT run_id, status, records_loaded, lower_bound, max_incremental_value "
            "FROM demo_fake._runs ORDER BY started_at"
        ).fetchall()
    assert rows == [(1, "a2"), (2, "b"), (3, "c")]
    assert runs == [
        (first.run_id, "succeeded", 3, "2024-01-01 00:00:00", "2024-03-01 12:00:00"),
        (second.run_id, "succeeded", 2, "2024-03-01 12:00:00", "2024-05-01 00:00:00"),
        (third.run_id, "succeeded", 4, "2024-01-01 00:00:00", "2024-05-01 00:00:00"),
    ]
