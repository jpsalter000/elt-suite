"""Postgres implementation of :class:`elt_suite.load.destination.Destination`."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from elt_suite.load.ddl import RUNS_COLUMNS, RUNS_TABLE, Column
from elt_suite.load.destination import RunRecord

DDL_LOCK_CLASS = 0x454C54  # "ELT": namespaces elt-suite's advisory locks


def _column_def(col: Column) -> sql.Composed:
    return sql.SQL("{} {}{}").format(
        sql.Identifier(col.name),
        sql.SQL(col.pg_type),
        sql.SQL("" if col.nullable else " NOT NULL"),
    )


def _adapt(value: Any) -> Any:
    return Jsonb(value) if isinstance(value, dict | list) else value


class PostgresDestination:
    def __init__(self, dsn: str) -> None:
        self.conn = psycopg.connect(dsn, autocommit=True)

    def _ensure(self, schema: str, table: str, columns: list[Column], pks: list[str]) -> None:
        with self.conn.transaction():
            # Parallel loaders race on IF NOT EXISTS DDL (catalog unique violations), so
            # DDL for one schema is serialized; the lock is released at commit.
            self.conn.execute(
                "SELECT pg_advisory_xact_lock(%s, hashtext(%s))", [DDL_LOCK_CLASS, schema]
            )
            self.conn.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
            )
            self.conn.execute(
                sql.SQL("CREATE TABLE IF NOT EXISTS {}.{} ({}, PRIMARY KEY ({}))").format(
                    sql.Identifier(schema),
                    sql.Identifier(table),
                    sql.SQL(", ").join(_column_def(c) for c in columns),
                    sql.SQL(", ").join(map(sql.Identifier, pks)),
                )
            )
            # Pick up fields added by a re-inferred schema. New columns stay nullable
            # since existing rows have no value for them.
            for col in columns:
                self.conn.execute(
                    sql.SQL("ALTER TABLE {}.{} ADD COLUMN IF NOT EXISTS {} {}").format(
                        sql.Identifier(schema),
                        sql.Identifier(table),
                        sql.Identifier(col.name),
                        sql.SQL(col.pg_type),
                    )
                )

    def ensure_table(
        self, schema: str, table: str, columns: list[Column], primary_keys: list[str]
    ) -> None:
        self._ensure(schema, RUNS_TABLE, RUNS_COLUMNS, ["run_id"])
        self._ensure(schema, table, columns, primary_keys)

    def upsert(
        self,
        schema: str,
        table: str,
        columns: list[str],
        primary_keys: list[str],
        rows: list[dict[str, Any]],
    ) -> None:
        if not rows:
            return
        updates = [c for c in columns if c not in primary_keys]
        stmt = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({}) ON CONFLICT ({}) DO {}").format(
            sql.Identifier(schema),
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.SQL(", ").join(sql.Placeholder() * len(columns)),
            sql.SQL(", ").join(map(sql.Identifier, primary_keys)),
            sql.SQL("UPDATE SET {}").format(
                sql.SQL(", ").join(
                    sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in updates
                )
            )
            if updates
            else sql.SQL("NOTHING"),
        )
        params = [[_adapt(row.get(c)) for c in columns] for row in rows]
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.executemany(stmt, params)

    def record_run(self, schema: str, run: RunRecord) -> None:
        self._ensure(schema, RUNS_TABLE, RUNS_COLUMNS, ["run_id"])
        self.upsert(
            schema,
            RUNS_TABLE,
            [c.name for c in RUNS_COLUMNS],
            ["run_id"],
            [asdict(run)],
        )

    def last_watermark(self, schema: str, consumer: str, job: str) -> str | None:
        self._ensure(schema, RUNS_TABLE, RUNS_COLUMNS, ["run_id"])
        row = self.conn.execute(
            sql.SQL(
                "SELECT max_incremental_value FROM {}.{} "
                "WHERE consumer = %s AND job = %s AND status = 'succeeded' "
                "AND max_incremental_value IS NOT NULL "
                "ORDER BY finished_at DESC LIMIT 1"
            ).format(sql.Identifier(schema), sql.Identifier(RUNS_TABLE)),
            [consumer, job],
        ).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self.conn.close()
