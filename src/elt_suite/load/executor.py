"""Generic full-run extract-and-load executor.

Consumes any configured job to completion through its consumer's fetch function
and upserts the records into the consumer's configured destination. Every record
is stamped with the run's ``run_id``, and the run itself is recorded in
``<target_schema>._runs``.

Incremental jobs resume from state: the lower bound of a run is the
``max_incremental_value`` of the job's last successful run (or the configured
``lower_bound`` if that is later, or if there is no prior run). The bound is
inclusive, so boundary records are re-read and idempotently upserted.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import batched
from typing import Any
from uuid import UUID, uuid4

from elt_suite.config import ConsumerConfig, JobConfig
from elt_suite.contract import Record, iter_records
from elt_suite.inference.executor import JobSchema
from elt_suite.inference.types import DATE, DATE_TIME, STRING
from elt_suite.load.ddl import LOADED_AT_COLUMN, RUN_ID_COLUMN, table_columns
from elt_suite.load.destination import Destination, RunRecord, connect_destination

log = logging.getLogger(__name__)
BATCH_SIZE = 1000


class LoadError(Exception):
    pass


@dataclass(frozen=True)
class RunResult:
    run_id: UUID
    records_loaded: int
    max_incremental_value: str | None


class _RecordPreparer:
    """Validates records against the inferred schema and coerces them for loading."""

    def __init__(self, schema: JobSchema, job: JobConfig, run_id: UUID) -> None:
        self.schema = schema
        self.run_id = run_id
        self.loaded_at = datetime.now(UTC)
        self.inc = job.incremental_loading
        self.max_incremental: tuple[datetime, str] | None = None
        self._temporal = {
            name: t.format
            for name, t in schema.fields.items()
            if t.type == STRING and t.format in (DATE, DATE_TIME)
        }

    def _parse_datetime(self, value: str) -> datetime:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            if self.inc:
                return datetime.strptime(value, self.inc.datetime_format)
            raise

    def _coerce(self, name: str, value: Any) -> Any:
        fmt = self._temporal.get(name)
        if not isinstance(value, str) or fmt is None:
            return value
        return date.fromisoformat(value) if fmt == DATE else self._parse_datetime(value)

    def _track_incremental(self, record: Record) -> None:
        if not self.inc:
            return
        raw = record.get(self.inc.incremental_key)
        if raw is None:
            return
        parsed = raw if isinstance(raw, datetime) else self._parse_datetime(str(raw))
        if self.max_incremental is None or parsed > self.max_incremental[0]:
            self.max_incremental = (parsed, self.inc.format(parsed))

    def __call__(self, index: int, record: Record) -> dict[str, Any]:
        unknown = record.keys() - self.schema.fields.keys()
        if unknown:
            raise LoadError(
                f"record #{index} has fields not in the inferred schema: {sorted(unknown)}; "
                f"re-run `elt infer {self.schema.consumer} --job {self.schema.job}`"
            )
        self._track_incremental(record)
        row = {name: self._coerce(name, value) for name, value in record.items()}
        row[RUN_ID_COLUMN] = self.run_id
        row[LOADED_AT_COLUMN] = self.loaded_at
        return row


def _dedupe(rows: Iterable[dict[str, Any]], pks: list[str]) -> list[dict[str, Any]]:
    """Keep the last row per primary key; Postgres rejects duplicate keys in one upsert."""
    return list({tuple(row.get(k) for k in pks): row for row in rows}.values())


def load_records(
    consumer: ConsumerConfig,
    job: JobConfig,
    schema: JobSchema,
    records: Iterator[Record],
    destination: Destination,
    lower_bound: datetime | None = None,
    run_id: UUID | None = None,
) -> RunResult:
    """Load ``records`` (already filtered at ``lower_bound``) and record the run."""
    run_id = run_id or uuid4()
    target = consumer.target_schema
    inc = job.incremental_loading
    columns = table_columns(schema)
    column_names = [c.name for c in columns]

    destination.ensure_table(target, job.name, columns, schema.primary_keys)
    run = RunRecord(
        run_id=run_id,
        consumer=consumer.name,
        job=job.name,
        status="running",
        started_at=datetime.now(UTC),
        lower_bound=inc.format(lower_bound) if inc and lower_bound else None,
    )
    destination.record_run(target, run)
    log.info("run %s started for %s.%s", run_id, consumer.name, job.name)

    prepare = _RecordPreparer(schema, job, run_id)
    loaded = 0
    try:
        for batch in batched(enumerate(records, start=1), BATCH_SIZE):
            rows = _dedupe((prepare(i, r) for i, r in batch), schema.primary_keys)
            destination.upsert(target, job.name, column_names, schema.primary_keys, rows)
            loaded += len(batch)
            log.info("loaded %d records", loaded)
    except BaseException as exc:  # incl. KeyboardInterrupt: never leave a run "running"
        run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"
        raise
    else:
        run.status = "succeeded"
    finally:
        run.finished_at = datetime.now(UTC)
        run.records_loaded = loaded
        run.max_incremental_value = prepare.max_incremental[1] if prepare.max_incremental else None
        destination.record_run(target, run)
        log.info("run %s %s (%d records)", run_id, run.status, loaded)

    return RunResult(run_id, loaded, run.max_incremental_value)


def resolve_lower_bound(
    consumer: ConsumerConfig, job: JobConfig, destination: Destination, full_refresh: bool = False
) -> datetime | None:
    """Where this run should start reading from; see the module docstring."""
    inc = job.incremental_loading
    if inc is None:
        return None
    configured = inc.lower_bound_dt
    if full_refresh:
        return configured
    watermark = destination.last_watermark(consumer.target_schema, consumer.name, job.name)
    if watermark is None:
        return configured
    return max(configured, inc.parse(watermark))


def extract_and_load(
    consumer: ConsumerConfig,
    job: JobConfig,
    schema: JobSchema,
    destination: Destination,
    full_refresh: bool = False,
) -> RunResult:
    lower_bound = resolve_lower_bound(consumer, job, destination, full_refresh)
    if lower_bound is not None:
        log.info("%s.%s: reading from %s", consumer.name, job.name, lower_bound.isoformat())
    records = iter_records(consumer, job, lower_bound)
    return load_records(consumer, job, schema, records, destination, lower_bound)


def run_full(consumer: ConsumerConfig, job: JobConfig, full_refresh: bool = False) -> RunResult:
    schema = JobSchema.load(consumer.name, job.name)
    if schema.primary_keys != job.primary_keys:
        raise LoadError(
            f"{consumer.name}.{job.name}: config primary_keys {job.primary_keys} differ from "
            f"schema {schema.primary_keys}; re-run inference"
        )
    destination = connect_destination(consumer.destination)
    try:
        return extract_and_load(consumer, job, schema, destination, full_refresh)
    finally:
        destination.close()
