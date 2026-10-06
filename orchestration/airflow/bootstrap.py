"""Create Airflow's metadata role and database on the warehouse instance, if missing.

Runs in the init container as the instance's admin login (PGUSER / PGPASSWORD, the
RDS master secret on AWS). Airflow itself then connects as its own role, so the
master secret's automatic rotation never breaks the running service. Re-running is
safe: an existing role gets its password reset to the current secret.
"""

from __future__ import annotations

import os

import psycopg2
from psycopg2 import sql


def main() -> None:
    role = os.environ.get("AIRFLOW_DB_USER", "airflow")
    database = os.environ.get("AIRFLOW_DB_NAME", "airflow")
    conn = psycopg2.connect(
        host=os.environ["AIRFLOW_DB_HOST"],
        port=os.environ.get("AIRFLOW_DB_PORT", "5432"),
        dbname=os.environ.get("BOOTSTRAP_DATABASE", "postgres"),
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        sslmode=os.environ.get("AIRFLOW_DB_SSLMODE", "prefer"),
    )
    conn.autocommit = True  # CREATE DATABASE cannot run inside a transaction
    password = os.environ["AIRFLOW_DB_PASSWORD"]
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
        verb = "ALTER" if cur.fetchone() else "CREATE"
        cur.execute(
            sql.SQL(verb + " ROLE {} WITH LOGIN PASSWORD %s").format(sql.Identifier(role)),
            [password],
        )
        print(f"bootstrap: {'updated' if verb == 'ALTER' else 'created'} role {role}")
        # Since PostgreSQL 16, creating a database owned by a role requires membership.
        cur.execute(sql.SQL("GRANT {} TO CURRENT_USER").format(sql.Identifier(role)))
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", [database])
        if cur.fetchone():
            print(f"bootstrap: database {database} exists")
        else:
            cur.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )
            print(f"bootstrap: created database {database}")
    conn.close()


if __name__ == "__main__":
    main()
