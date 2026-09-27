import os
from datetime import datetime
from uuid import UUID

import pytest

from elt_suite.contract import iter_records
from elt_suite.inference import JobSchema, run_inference
from elt_suite.load import LoadError, load_records, run_full
from elt_suite.load.ddl import RUN_ID_COLUMN, table_columns


class MemoryDestination:
    """In-memory Destination used to test the executor without a database."""

    def __init__(self):
        self.tables: dict[tuple[str, str], dict[tuple, dict]] = {}
        self.runs: dict[UUID, list] = {}

    def ensure_table(self, schema, table, columns, primary_keys):
        self.tables.setdefault((schema, table), {})

    def upsert(self, schema, table, columns, primary_keys, rows):
        target = self.tables[(schema, table)]
        for row in rows:
            target[tuple(row[k] for k in primary_keys)] = {c: row.get(c) for c in columns}

    def record_run(self, schema, run):
        self.runs.setdefault(run.run_id, []).append((schema, run.status, run.records_loaded))
        self.last_run = run

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
    result = load_records(consumer, job, schema, iter_records(consumer, job), dest)

    rows = dest.tables[("demo_fake", "widgets")]
    assert len(rows) == 2  # id=1 upserted, last write wins
    assert rows[(1,)]["name"] == "a2"
    assert {r[RUN_ID_COLUMN] for r in rows.values()} == {result.run_id}
    assert isinstance(rows[(1,)]["updated_at"], datetime)

    assert dest.runs[result.run_id] == [
        ("demo_fake", "running", None),
        ("demo_fake", "succeeded", 3),
    ]
    assert dest.last_run.lower_bound == "2024-01-01 00:00:00"
    assert result.max_incremental_value == "2024-03-01 12:00:00"


def test_load_fails_on_unknown_field_and_records_failure(inferred, fake_client):
    consumer, job, schema = inferred
    fake_client.append({"id": 3, "surprise": 1, "updated_at": "2024-04-01 00:00:00"})
    dest = MemoryDestination()
    with pytest.raises(LoadError, match=r"not in the inferred schema: \['surprise'\]"):
        load_records(consumer, job, schema, iter_records(consumer, job), dest)
    assert dest.last_run.status == "failed"
    assert "surprise" in dest.last_run.error


def test_run_full_requires_schema(fake_consumer):
    from elt_suite.inference import InferenceError

    with pytest.raises(InferenceError, match="elt infer"):
        run_full(fake_consumer, fake_consumer.jobs[0])


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("WAREHOUSE_DSN"), reason="WAREHOUSE_DSN not set")
def test_postgres_end_to_end(inferred):
    import psycopg

    consumer, job, _ = inferred
    with psycopg.connect(os.environ["WAREHOUSE_DSN"], autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS demo_fake CASCADE")

    first = run_full(consumer, job)
    second = run_full(consumer, job)

    with psycopg.connect(os.environ["WAREHOUSE_DSN"]) as conn:
        rows = conn.execute(
            "SELECT id, name, _run_id FROM demo_fake.widgets ORDER BY id"
        ).fetchall()
        runs = conn.execute(
            "SELECT run_id, status, records_loaded FROM demo_fake._runs ORDER BY started_at"
        ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [(1, "a2"), (2, "b")]
    assert {r[2] for r in rows} == {second.run_id}
    assert runs == [(first.run_id, "succeeded", 3), (second.run_id, "succeeded", 3)]
