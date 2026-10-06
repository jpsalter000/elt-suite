"""Airflow DAGs for every elt-suite pipeline, built from the graphs baked into the image.

ELT_GRAPH_DIR points at the output of ``elt pipeline export``; ELT_RUNNER chooses
whether tasks run as ECS tasks (``ecs``) or through a local elt CLI (``local``).
"""

import os

from elt_airflow import build_dags, runner_from_env

globals().update(
    build_dags(os.environ.get("ELT_GRAPH_DIR", "/opt/elt/graphs"), runner_from_env(os.environ))
)
