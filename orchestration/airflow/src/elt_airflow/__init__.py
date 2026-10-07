"""Airflow adapter for elt-suite.

elt-suite pipelines are orchestrator-agnostic: ``elt pipeline export DIR`` writes
each pipeline's task graph as JSON, and every task runs with
``elt pipeline run-task <pipeline> <task>``. This package turns those graphs into
Airflow DAGs whose operators run that command, on ECS or locally.
"""

from elt_airflow.dags import build_dag, build_dags
from elt_airflow.graphs import GraphError, load_graphs
from elt_airflow.runners import EcsRunner, LocalRunner, Runner, runner_from_env

__all__ = [
    "EcsRunner",
    "GraphError",
    "LocalRunner",
    "Runner",
    "build_dag",
    "build_dags",
    "load_graphs",
    "runner_from_env",
]
