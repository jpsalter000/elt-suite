"""Map inferred schema types to warehouse column definitions."""

from __future__ import annotations

from dataclasses import dataclass

from elt_suite.inference.executor import JobSchema
from elt_suite.inference.types import (
    ARRAY,
    BOOLEAN,
    DATE,
    DATE_TIME,
    INTEGER,
    NULL,
    NUMBER,
    OBJECT,
    STRING,
    SchemaType,
)

RUN_ID_COLUMN = "_run_id"
LOADED_AT_COLUMN = "_loaded_at"
RUNS_TABLE = "_runs"


@dataclass(frozen=True)
class Column:
    name: str
    pg_type: str
    nullable: bool = True


def pg_type(t: SchemaType) -> str:
    if t.type == STRING:
        return {DATE: "DATE", DATE_TIME: "TIMESTAMPTZ"}.get(t.format or "", "TEXT")
    return {
        NULL: "TEXT",  # only ever seen null: keep it loadable, narrow later
        BOOLEAN: "BOOLEAN",
        INTEGER: "BIGINT",
        NUMBER: "DOUBLE PRECISION",
        OBJECT: "JSONB",
        ARRAY: "JSONB",
    }[t.type]


def table_columns(schema: JobSchema) -> list[Column]:
    """Data columns from the inferred schema plus the run-metadata columns."""
    columns = [
        Column(name, pg_type(t), nullable=name not in schema.primary_keys)
        for name, t in schema.fields.items()
    ]
    columns.append(Column(RUN_ID_COLUMN, "UUID", nullable=False))
    columns.append(Column(LOADED_AT_COLUMN, "TIMESTAMPTZ", nullable=False))
    return columns


RUNS_COLUMNS = [
    Column("run_id", "UUID", nullable=False),
    Column("consumer", "TEXT", nullable=False),
    Column("job", "TEXT", nullable=False),
    Column("status", "TEXT", nullable=False),
    Column("started_at", "TIMESTAMPTZ", nullable=False),
    Column("finished_at", "TIMESTAMPTZ"),
    Column("records_loaded", "BIGINT"),
    Column("lower_bound", "TEXT"),
    Column("max_incremental_value", "TEXT"),
    Column("error", "TEXT"),
]
