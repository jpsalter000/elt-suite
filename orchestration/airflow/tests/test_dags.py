"""The Airflow adapter: one DAG per exported elt-suite pipeline graph.

The adapter never imports elt_suite. It reads the versioned JSON graphs written by
``elt pipeline export`` and turns each task into an operator that runs the task's
``argv`` with the elt CLI: in an ECS task (EcsRunner) or in a local virtualenv
(LocalRunner). These tests run inside the Airflow image.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from elt_airflow import EcsRunner, GraphError, LocalRunner, build_dags, load_graphs, runner_from_env

FIXTURES = Path(__file__).parent / "fixtures"
DAGS_FOLDER = Path(__file__).parents[1] / "dags"
ECS = EcsRunner(
    cluster="elt-suite-dev",
    task_definition="elt-suite-dev",
    subnets=["subnet-a", "subnet-b"],
    security_groups=["sg-task"],
    region="us-east-1",
    log_group="/ecs/elt-suite-dev",
)


@pytest.fixture
def graphs(tmp_path):
    shutil.copy(FIXTURES / "demo_daily.json", tmp_path)
    return tmp_path


def _edges(dag):
    return {(up, task.task_id) for task in dag.tasks for up in task.upstream_task_ids}


# --- DAG structure ---------------------------------------------------------------------


def test_one_dag_per_graph_with_the_graphs_tasks_and_edges(graphs):
    dags = build_dags(graphs, ECS)
    assert list(dags) == ["demo_daily"]
    dag = dags["demo_daily"]
    assert {t.task_id for t in dag.tasks} == {"extract.widgets", "extract.gadgets", "transform"}
    assert _edges(dag) == {("extract.widgets", "transform"), ("extract.gadgets", "transform")}


def test_dags_follow_the_pipeline_schedule_without_backfills_or_overlap(graphs):
    dag = build_dags(graphs, ECS)["demo_daily"]
    assert str(dag.timetable.summary) == "0 6 * * *"
    assert dag.catchup is False
    assert dag.max_active_runs == 1
    assert "elt-suite" in dag.tags
    assert "Two extract tasks" in dag.doc_md


def test_a_pipeline_without_a_schedule_is_triggered_manually(graphs):
    data = json.loads((graphs / "demo_daily.json").read_text())
    data["schedule"] = None
    (graphs / "demo_daily.json").write_text(json.dumps(data))
    dag = build_dags(graphs, ECS)["demo_daily"]
    assert dag.timetable.summary in ("None", "Never, external triggers only")


# --- runners ---------------------------------------------------------------------------


def test_ecs_runner_runs_the_task_command_in_the_elt_container(graphs):
    from airflow.providers.amazon.aws.operators.ecs import EcsRunTaskOperator

    task = build_dags(graphs, ECS)["demo_daily"].get_task("extract.widgets")
    assert isinstance(task, EcsRunTaskOperator)
    assert task.cluster == "elt-suite-dev" and task.task_definition == "elt-suite-dev"
    assert task.launch_type == "FARGATE"
    assert task.overrides == {
        "containerOverrides": [
            {"name": "elt", "command": ["pipeline", "run-task", "demo_daily", "extract.widgets"]}
        ]
    }
    assert task.network_configuration == {
        "awsvpcConfiguration": {
            "subnets": ["subnet-a", "subnet-b"],
            "securityGroups": ["sg-task"],
            "assignPublicIp": "ENABLED",  # no NAT: tasks reach AWS APIs over the IGW
        }
    }
    # The runner's log configuration uses stream prefix "elt" for container "elt".
    assert task.awslogs_group == "/ecs/elt-suite-dev"
    assert task.awslogs_stream_prefix == "elt/elt"
    assert task.deferrable is True


def test_local_runner_runs_the_elt_cli_from_its_virtualenv(graphs):
    from airflow.providers.standard.operators.bash import BashOperator

    runner = LocalRunner(elt="/opt/elt/.venv/bin/elt")
    task = build_dags(graphs, runner)["demo_daily"].get_task("transform")
    assert isinstance(task, BashOperator)
    assert task.bash_command == "/opt/elt/.venv/bin/elt pipeline run-task demo_daily transform"


def test_runner_from_env_picks_the_runner_and_reports_missing_settings():
    local = runner_from_env({"ELT_RUNNER": "local"})
    assert isinstance(local, LocalRunner)

    ecs = runner_from_env(
        {
            "ELT_RUNNER": "ecs",
            "ELT_ECS_CLUSTER": "c",
            "ELT_ECS_TASK_DEFINITION": "td",
            "ELT_ECS_SUBNETS": "subnet-a, subnet-b",
            "ELT_ECS_SECURITY_GROUPS": "sg-task",
            "ELT_ECS_LOG_GROUP": "/ecs/x",
            "AWS_DEFAULT_REGION": "us-east-1",
        }
    )
    assert isinstance(ecs, EcsRunner) and ecs.subnets == ["subnet-a", "subnet-b"]

    with pytest.raises(GraphError, match=r"ELT_ECS_CLUSTER.*ELT_ECS_SUBNETS"):
        runner_from_env({"ELT_RUNNER": "ecs", "ELT_ECS_TASK_DEFINITION": "td"})
    with pytest.raises(GraphError, match="ELT_RUNNER must be 'ecs' or 'local'"):
        runner_from_env({"ELT_RUNNER": "k8s"})


# --- graph validation ------------------------------------------------------------------


def test_graphs_of_an_unknown_version_are_rejected(graphs):
    data = json.loads((graphs / "demo_daily.json").read_text())
    data["version"] = 2
    (graphs / "demo_daily.json").write_text(json.dumps(data))
    with pytest.raises(GraphError, match=r"version 2.*supports version 1"):
        load_graphs(graphs)


def test_a_missing_graph_directory_says_how_to_create_it(tmp_path):
    with pytest.raises(GraphError, match="elt pipeline export"):
        load_graphs(tmp_path / "nowhere")


def test_dependencies_must_name_tasks_in_the_graph(graphs):
    data = json.loads((graphs / "demo_daily.json").read_text())
    data["tasks"][2]["depends_on"].append("extract.sprockets")
    (graphs / "demo_daily.json").write_text(json.dumps(data))
    with pytest.raises(GraphError, match="extract.sprockets"):
        load_graphs(graphs)


# --- the DAGs folder -------------------------------------------------------------------


def test_the_dags_folder_imports_cleanly_with_the_baked_graphs(monkeypatch, graphs):
    from airflow.models.dagbag import DagBag

    monkeypatch.setenv("ELT_GRAPH_DIR", os.environ.get("ELT_GRAPH_DIR", str(graphs)))
    monkeypatch.setenv("ELT_RUNNER", "local")
    bag = DagBag(dag_folder=str(DAGS_FOLDER), include_examples=False)
    assert bag.import_errors == {}
    assert bag.dags, "no DAGs were built"


def test_the_image_bakes_the_utilization_pipeline():
    baked = Path(os.environ.get("ELT_GRAPH_DIR", "/opt/elt/graphs"))
    if not baked.is_dir():
        pytest.skip("not running inside the Airflow image")
    (graph,) = [g for g in load_graphs(baked) if g["pipeline"] == "utilization_daily"]
    kinds = [t["kind"] for t in graph["tasks"]]
    assert kinds.count("extract_load") == 10 and kinds[-1] == "dbt"


@pytest.mark.skipif(
    not (os.environ.get("WAREHOUSE_DSN") and os.environ.get("VANDELAY_NS_BASE_URL")),
    reason="needs the warehouse and the NetSuite mock (docker compose --profile airflow)",
)
def test_local_runner_runs_the_utilization_pipeline_end_to_end():
    baked = Path(os.environ.get("ELT_GRAPH_DIR", "/opt/elt/graphs"))
    dag = build_dags(baked, LocalRunner())["utilization_daily"]
    run = dag.test()
    assert run.state == "success", {ti.task_id: ti.state for ti in run.get_task_instances()}
