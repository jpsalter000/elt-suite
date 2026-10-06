"""Build one Airflow DAG per elt-suite pipeline graph."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pendulum

from elt_airflow.graphs import load_graphs
from elt_airflow.runners import Runner

START_DATE = pendulum.datetime(2026, 1, 1, tz="UTC")
DEFAULT_ARGS = {"retries": 1, "retry_delay": timedelta(minutes=5)}


def build_dag(graph: dict[str, Any], runner: Runner) -> Any:
    from airflow.sdk import DAG

    with DAG(
        dag_id=graph["pipeline"],
        description=f"elt-suite pipeline {graph['pipeline']}",
        doc_md=graph.get("description") or None,
        schedule=graph.get("schedule"),
        start_date=START_DATE,
        catchup=False,
        max_active_runs=1,  # two runs would rebuild the same dbt views at once
        default_args=DEFAULT_ARGS,
        tags=["elt-suite"],
    ) as dag:
        operators = {task["id"]: runner.operator(task) for task in graph["tasks"]}
        for task in graph["tasks"]:
            for upstream in task.get("depends_on", []):
                operators[upstream] >> operators[task["id"]]
    return dag


def build_dags(graph_dir: str | Path, runner: Runner) -> dict[str, Any]:
    return {graph["pipeline"]: build_dag(graph, runner) for graph in load_graphs(graph_dir)}
