# syntax=docker/dockerfile:1
# Build from the repository root:
#   docker build -f orchestration/airflow/Dockerfile --target airflow -t elt-suite-airflow .
ARG AIRFLOW_VERSION=3.3.2
ARG PYTHON_VERSION=3.12

# ---- the elt CLI in its own virtualenv --------------------------------------------------
# dbt and Airflow pin incompatible libraries (protobuf), so Airflow never imports
# elt_suite: the adapter reads exported graphs and LocalRunner shells out to this venv.
FROM ghcr.io/astral-sh/uv:0.9.1 AS uv
FROM python:${PYTHON_VERSION}-slim AS elt

COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/elt/.venv

WORKDIR /opt/elt/app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --no-editable
COPY config ./config
COPY schemas ./schemas
COPY transform ./transform
RUN rm -rf src && ELT_HOME=/opt/elt/app /opt/elt/.venv/bin/elt pipeline export /opt/elt/graphs

# ---- Airflow with the elt-suite adapter --------------------------------------------------
FROM apache/airflow:${AIRFLOW_VERSION}-python${PYTHON_VERSION} AS airflow
ARG AIRFLOW_VERSION
ARG PYTHON_VERSION

COPY --chown=airflow:root orchestration/airflow/pyproject.toml /opt/elt-airflow/pyproject.toml
COPY --chown=airflow:root orchestration/airflow/src /opt/elt-airflow/src
RUN pip install --no-cache-dir /opt/elt-airflow "apache-airflow==${AIRFLOW_VERSION}" \
      "apache-airflow-providers-fab" "aiobotocore" \
      --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"

COPY --from=elt /opt/elt /opt/elt
COPY --chown=airflow:root orchestration/airflow/dags /opt/airflow/dags
COPY --chown=airflow:root --chmod=0755 orchestration/airflow/entrypoint.sh /opt/elt-airflow/entrypoint.sh
COPY --chown=airflow:root orchestration/airflow/bootstrap.py /opt/elt-airflow/bootstrap.py
COPY --chown=airflow:root --chmod=0755 orchestration/airflow/db-url /opt/elt-airflow/db-url

ENV ELT_GRAPH_DIR=/opt/elt/graphs \
    ELT_HOME=/opt/elt/app \
    ELT_BIN=/opt/elt/.venv/bin/elt \
    ELT_RUNNER=local \
    AIRFLOW__DATABASE__SQL_ALCHEMY_CONN_CMD="/opt/elt-airflow/db-url sync" \
    AIRFLOW__DATABASE__SQL_ALCHEMY_CONN_ASYNC_CMD="/opt/elt-airflow/db-url async" \
    AIRFLOW__CORE__LOAD_EXAMPLES=False \
    AIRFLOW__CORE__EXECUTOR=LocalExecutor \
    AIRFLOW__CORE__AUTH_MANAGER=airflow.providers.fab.auth_manager.fab_auth_manager.FabAuthManager

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/opt/elt-airflow/entrypoint.sh"]

# ---- test: the adapter's tests, run in CI against the image above ------------------------
FROM airflow AS test
RUN pip install --no-cache-dir pytest
COPY --chown=airflow:root orchestration/airflow/tests /opt/elt-airflow/tests
WORKDIR /opt/elt-airflow
ENTRYPOINT []
CMD ["python", "-m", "pytest", "-v", "-rs", "tests"]
