"""Running the dbt project in ``transform/`` from a pipeline task.

dbt reads its connection from ``DBT_PG_*`` environment variables (see
``transform/profiles.yml``). They are derived from ``WAREHOUSE_DSN`` the way libpq
resolves a connection: values in the DSN win, and ``PG*`` variables fill the
gaps. That is how the ECS task supplies its RDS credentials.

Because the container's root filesystem is read-only, dbt's target and log
directories default to the system temp directory.
"""

from __future__ import annotations

import getpass
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

from elt_suite import paths
from elt_suite.config import ConfigError

DBT_LOCK_CLASS = 0x444254  # "DBT": namespaces the per-pipeline advisory lock

_PARAMS = {  # DBT_PG_* name -> (conninfo key, PG* variable, default)
    "DBT_PG_HOST": ("host", "PGHOST", "localhost"),
    "DBT_PG_PORT": ("port", "PGPORT", "5432"),
    "DBT_PG_USER": ("user", "PGUSER", None),
    "DBT_PG_PASSWORD": ("password", "PGPASSWORD", ""),
    "DBT_PG_DBNAME": ("dbname", "PGDATABASE", None),
    "DBT_PG_SSLMODE": ("sslmode", "PGSSLMODE", "prefer"),
}


@dataclass(frozen=True)
class DbtOutcome:
    success: bool
    summary: dict[str, int] = field(default_factory=dict)  # node status -> count
    failures: list[str] = field(default_factory=list)


def connection_env(dsn: str, environ: Mapping[str, str]) -> dict[str, str]:
    """``DBT_PG_*`` settings for ``dsn``, resolved with libpq's precedence."""
    params = {k: str(v) for k, v in conninfo_to_dict(dsn).items() if v not in (None, "")}
    env: dict[str, str] = {}
    for name, (key, pg_var, default) in _PARAMS.items():
        value = params.get(key) or environ.get(pg_var) or default
        if value is None and key == "user":
            value = getpass.getuser()
        if value is None and key == "dbname":
            value = env["DBT_PG_USER"]
        env[name] = str(value)
    return env


def _dbt_environment() -> dict[str, str]:
    dsn = os.environ.get("WAREHOUSE_DSN")
    if not dsn:
        raise ConfigError("dbt step: environment variable WAREHOUSE_DSN is not set")
    scratch = Path(tempfile.gettempdir()) / "elt-dbt"
    return {
        **connection_env(dsn, os.environ),
        "DBT_TARGET_PATH": os.environ.get("DBT_TARGET_PATH", str(scratch / "target")),
        "DBT_LOG_PATH": os.environ.get("DBT_LOG_PATH", str(scratch / "logs")),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    }


def _outcome(result: Any) -> DbtOutcome:
    summary: dict[str, int] = {}
    failures: list[str] = []
    for node in getattr(result.result, "results", None) or []:
        status = str(node.status)
        summary[status] = summary.get(status, 0) + 1
        if status in ("error", "fail", "runtime error"):
            failures.append(f"{node.node.unique_id}: {node.message}")
    if result.exception is not None:
        failures.append(f"{type(result.exception).__name__}: {result.exception}")
    return DbtOutcome(success=bool(result.success), summary=summary, failures=failures)


def invoke_dbt(args: list[str], *, lock_key: str) -> DbtOutcome:
    """Run ``dbt <args>`` in-process while holding the pipeline's advisory lock.

    The lock keeps two runs of the same pipeline from rebuilding the same views at
    the same time. It is released when the connection closes, even after a crash.
    """
    from dbt.cli.main import dbtRunner  # imported lazily: dbt is slow to import

    env = _dbt_environment()
    os.environ.update(env)  # profiles.yml reads env_var() from the process environment
    project = str(paths.dbt_project_dir())
    with psycopg.connect(os.environ["WAREHOUSE_DSN"], autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s, hashtext(%s))", [DBT_LOCK_CLASS, lock_key])
        result = dbtRunner().invoke([*args, "--project-dir", project, "--profiles-dir", project])
    return _outcome(result)
