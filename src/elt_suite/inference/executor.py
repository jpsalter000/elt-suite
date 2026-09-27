"""Generic schema-inference executor.

Runs any configured job to completion through its consumer's fetch function,
infers a type for every record and merges them into one schema per job, written
to ``schemas/<consumer>/<job>.json``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elt_suite import paths
from elt_suite.config import ConsumerConfig, JobConfig
from elt_suite.contract import Record, iter_records
from elt_suite.inference.types import (
    IncompatibleTypeError,
    SchemaType,
    infer_value,
    merge_properties,
)

log = logging.getLogger(__name__)


class InferenceError(Exception):
    pass


@dataclass(frozen=True)
class JobSchema:
    consumer: str
    job: str
    primary_keys: list[str]
    incremental_key: str | None
    generated_at: str
    records_scanned: int
    fields: dict[str, SchemaType]

    def to_json(self) -> dict[str, Any]:
        return {
            "consumer": self.consumer,
            "job": self.job,
            "primary_keys": self.primary_keys,
            "incremental_key": self.incremental_key,
            "generated_at": self.generated_at,
            "records_scanned": self.records_scanned,
            "fields": {k: v.to_json() for k, v in self.fields.items()},
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> JobSchema:
        return cls(
            consumer=data["consumer"],
            job=data["job"],
            primary_keys=data["primary_keys"],
            incremental_key=data.get("incremental_key"),
            generated_at=data["generated_at"],
            records_scanned=data["records_scanned"],
            fields={k: SchemaType.from_json(v) for k, v in data["fields"].items()},
        )

    @classmethod
    def load(cls, consumer: str, job: str) -> JobSchema:
        path = paths.schema_file(consumer, job)
        if not path.exists():
            raise InferenceError(
                f"no schema at {path}; run `elt infer {consumer} --job {job}` first"
            )
        return cls.from_json(json.loads(path.read_text(encoding="utf-8")))

    def write(self) -> Path:
        path = paths.schema_file(self.consumer, self.job)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=2) + "\n", encoding="utf-8")
        return path


def infer_schema(consumer: ConsumerConfig, job: JobConfig, records: Iterable[Record]) -> JobSchema:
    """Fold every record into a single field map, failing on incompatible types."""
    inc = job.incremental_loading
    formats = [inc.datetime_format] if inc else []
    fields: dict[str, SchemaType] = {}
    count = 0
    for count, record in enumerate(records, start=1):
        observed = infer_value(record, formats).properties
        try:
            fields = observed if count == 1 else merge_properties(fields, observed)
        except IncompatibleTypeError as exc:
            raise InferenceError(f"{consumer.name}.{job.name}: record #{count}: {exc}") from exc

    if count == 0:
        raise InferenceError(f"{consumer.name}.{job.name}: no records returned; cannot infer")
    required = [*job.primary_keys, *([inc.incremental_key] if inc else [])]
    missing = [k for k in required if k not in fields]
    if missing:
        raise InferenceError(f"{consumer.name}.{job.name}: key fields never observed: {missing}")
    nullable_pks = [k for k in job.primary_keys if fields[k].nullable]
    if nullable_pks:
        raise InferenceError(
            f"{consumer.name}.{job.name}: primary key fields null or missing in some "
            f"records: {nullable_pks}"
        )

    return JobSchema(
        consumer=consumer.name,
        job=job.name,
        primary_keys=list(job.primary_keys),
        incremental_key=inc.incremental_key if inc else None,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        records_scanned=count,
        fields=fields,
    )


def run_inference(consumer: ConsumerConfig, job: JobConfig) -> Path:
    log.info("inferring schema for %s.%s", consumer.name, job.name)
    schema = infer_schema(consumer, job, iter_records(consumer, job))
    path = schema.write()
    log.info("scanned %d records -> %s", schema.records_scanned, path)
    return path
