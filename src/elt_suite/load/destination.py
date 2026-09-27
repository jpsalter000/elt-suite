"""Destination interface for the load executor, and a factory for configured ones."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from elt_suite.config import ConfigError, load_destination
from elt_suite.load.ddl import Column


@dataclass
class RunRecord:
    run_id: UUID
    consumer: str
    job: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    records_loaded: int | None = None
    lower_bound: str | None = None
    max_incremental_value: str | None = None
    error: str | None = None


class Destination(Protocol):
    def ensure_table(
        self, schema: str, table: str, columns: list[Column], primary_keys: list[str]
    ) -> None: ...

    def upsert(
        self,
        schema: str,
        table: str,
        columns: list[str],
        primary_keys: list[str],
        rows: list[dict[str, Any]],
    ) -> None: ...

    def record_run(self, schema: str, run: RunRecord) -> None:
        """Insert or update the run's row in ``<schema>._runs``."""
        ...

    def last_watermark(self, schema: str, consumer: str, job: str) -> str | None:
        """``max_incremental_value`` of the job's most recent successful run, if any."""
        ...

    def close(self) -> None: ...


def connect_destination(name: str) -> Destination:
    cfg = load_destination(name)
    dsn = os.environ.get(cfg.dsn_env)
    if not dsn:
        raise ConfigError(f"destination {name!r}: environment variable {cfg.dsn_env} is not set")
    if cfg.type == "postgres":
        from elt_suite.load.postgres import PostgresDestination

        return PostgresDestination(dsn)
    raise ConfigError(f"unsupported destination type {cfg.type!r}")
