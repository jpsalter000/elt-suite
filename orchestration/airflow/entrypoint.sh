#!/usr/bin/env bash
# Entrypoint for the elt-suite Airflow image.
#
# 1. Builds Airflow's database URLs from parts, so passwords with special characters
#    survive: AIRFLOW_DB_HOST, AIRFLOW_DB_PORT (5432), AIRFLOW_DB_NAME (airflow),
#    AIRFLOW_DB_USER (airflow), AIRFLOW_DB_PASSWORD, AIRFLOW_DB_SSLMODE (prefer).
# 2. `bootstrap` (the init container): create Airflow's role and database, migrate,
#    and create the admin user. Every step is idempotent.
# 3. Anything else goes to the official image's entrypoint (`api-server`, ...).
set -euo pipefail

if [[ -n "${AIRFLOW_DB_HOST:-}" && -z "${AIRFLOW__DATABASE__SQL_ALCHEMY_CONN:-}" ]]; then
  urlencode() { python -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"; }
  user=$(urlencode "${AIRFLOW_DB_USER:-airflow}")
  password=$(urlencode "${AIRFLOW_DB_PASSWORD}")
  location="${AIRFLOW_DB_HOST}:${AIRFLOW_DB_PORT:-5432}/${AIRFLOW_DB_NAME:-airflow}"
  sslmode="${AIRFLOW_DB_SSLMODE:-prefer}"
  export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="postgresql+psycopg2://${user}:${password}@${location}?sslmode=${sslmode}"
  # asyncpg spells it ssl=, not sslmode=.
  export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN_ASYNC="postgresql+asyncpg://${user}:${password}@${location}?ssl=${sslmode}"
fi

if [[ "${1:-}" == "bootstrap" ]]; then
  python /opt/elt-airflow/bootstrap.py
  airflow db migrate
  admin="${_AIRFLOW_WWW_USER_USERNAME:-admin}"
  if airflow users list --output plain | awk '{print $2}' | grep -qx "${admin}"; then
    echo "bootstrap: admin user ${admin} exists"
  else
    airflow users create --username "${admin}" --password "${_AIRFLOW_WWW_USER_PASSWORD}" \
      --role Admin --firstname elt --lastname admin --email "${admin}@example.invalid"
  fi
  exit 0
fi

exec /entrypoint "$@"
