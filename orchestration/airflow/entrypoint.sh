#!/usr/bin/env bash
# Entrypoint for the elt-suite Airflow image.
#
# `bootstrap` (the init container): create Airflow's role and database, migrate,
# and create the admin user. Every step is idempotent.
# Anything else goes to the official image's entrypoint (`api-server`, ...).
#
# The database URL comes from /opt/elt-airflow/db-url through
# AIRFLOW__DATABASE__SQL_ALCHEMY_CONN_CMD, so nothing needs exporting here.
set -euo pipefail

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
