"""Pipelines: the orchestrator-agnostic layer that chains extract-and-load jobs and dbt.

A pipeline (``config/pipelines/<name>.json``) is a small DAG of steps. ``plan()``
expands it into tasks, one per extract job plus one per dbt step, each runnable on
its own with ``elt pipeline run-task <pipeline> <task>``. Every orchestrator wraps
that same contract: the built-in ``elt pipeline run``, an Airflow DAG, or anything
that can start a container.
"""

import copy
import json
from uuid import uuid4

import pytest
from typer.testing import CliRunner

import elt_suite.pipeline.execute as execute
from conftest import FAKE_CONFIG, write_consumer
from elt_suite.cli import app
from elt_suite.config import ConfigError
from elt_suite.contract import SourceError
from elt_suite.load import RunResult
from elt_suite.pipeline import (
    GRAPH_VERSION,
    DbtOutcome,
    connection_env,
    graph,
    list_pipelines,
    load_pipeline,
    plan,
    run_pipeline,
    run_task,
)

TWO_JOBS = {
    **copy.deepcopy(FAKE_CONFIG),
    "jobs": [
        *copy.deepcopy(FAKE_CONFIG["jobs"]),
        {"name": "gadgets", "primary_keys": ["id"]},
    ],
}
PIPELINE = {
    "name": "demo_daily",
    "description": "Widgets and gadgets, then the dbt project.",
    "schedule": "0 6 * * *",
    "steps": [
        {"id": "extract", "type": "extract_load", "consumer": "demo_fake_extract_and_load"},
        {"id": "transform", "type": "dbt", "command": "build", "depends_on": ["extract"]},
    ],
}


def write_pipeline(home, config, name=None):
    folder = home / "config" / "pipelines"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name or config['name']}.json").write_text(json.dumps(config))
    return config["name"]


@pytest.fixture
def home(elt_home):
    write_consumer(elt_home, TWO_JOBS)
    write_pipeline(elt_home, PIPELINE)
    return elt_home


@pytest.fixture
def calls(monkeypatch):
    """Record extract-and-load and dbt calls instead of touching a database."""
    log: list[tuple] = []
    outcomes: dict[str, object] = {}

    def fake_run_full(consumer, job, full_refresh=False):
        log.append(("extract", consumer.name, job.name, full_refresh))
        outcome = outcomes.get(job.name)
        if isinstance(outcome, BaseException):
            raise outcome
        return RunResult(run_id=uuid4(), records_loaded=3, max_incremental_value=None)

    def fake_invoke(args, *, lock_key):
        log.append(("dbt", tuple(args), lock_key))
        return outcomes.get("dbt", DbtOutcome(success=True, summary={"pass": 5}, failures=[]))

    monkeypatch.setattr(execute, "run_full", fake_run_full)
    monkeypatch.setattr(execute, "invoke_dbt", fake_invoke)
    return log, outcomes


# --- configuration ---------------------------------------------------------------------


def test_loads_and_lists_pipelines(home):
    pipeline = load_pipeline("demo_daily")
    assert pipeline.schedule == "0 6 * * *"
    assert [s.id for s in pipeline.steps] == ["extract", "transform"]
    assert list_pipelines() == ["demo_daily"]


def test_the_file_name_must_match_the_pipeline_name(home):
    write_pipeline(home, PIPELINE, name="other")
    with pytest.raises(ConfigError, match="rename the file"):
        load_pipeline("other")


def test_a_missing_pipeline_lists_the_configured_ones(home):
    with pytest.raises(ConfigError, match=r"no pipeline 'nightly'.*demo_daily"):
        load_pipeline("nightly")


def _mutated(mutate):
    config = copy.deepcopy(PIPELINE)
    mutate(config)
    return config


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c["steps"].append(copy.deepcopy(c["steps"][0])), "duplicate step ids"),
        (lambda c: c["steps"][1].update(depends_on=["load"]), r"unknown step.*load"),
        (lambda c: c["steps"][0].update(depends_on=["transform"]), "cycle"),
        (lambda c: c["steps"][0].update(type="spark"), "type"),
        (lambda c: c.update(steps=[]), "steps"),
        (lambda c: c["steps"][1].update(select="staging"), r"descendants.*\+"),
        (lambda c: c["steps"][1].update(command="docs"), "command"),
    ],
)
def test_invalid_pipelines_are_rejected_with_a_reason(home, mutate, message):
    write_pipeline(home, _mutated(mutate))
    with pytest.raises(ConfigError, match=message):
        load_pipeline("demo_daily")


def test_unknown_consumers_and_jobs_are_rejected(home):
    write_pipeline(home, _mutated(lambda c: c["steps"][0].update(consumer="nope")))
    with pytest.raises(ConfigError, match=r"nope.*demo_fake_extract_and_load"):
        load_pipeline("demo_daily")
    write_pipeline(home, _mutated(lambda c: c["steps"][0].update(jobs=["sprockets"])))
    with pytest.raises(ConfigError, match=r"sprockets.*widgets"):
        load_pipeline("demo_daily")


def test_repo_pipelines_are_valid(monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("ELT_HOME", str(Path(__file__).parents[1]))
    assert "utilization_daily" in list_pipelines()
    for name in list_pipelines():
        load_pipeline(name)


# --- planning --------------------------------------------------------------------------


def test_extract_steps_expand_to_one_task_per_job(home):
    tasks = plan(load_pipeline("demo_daily"))
    assert [t.id for t in tasks] == ["extract.widgets", "extract.gadgets", "transform"]
    assert tasks[0].kind == "extract_load" and tasks[0].job == "widgets"
    assert tasks[2].depends_on == ("extract.widgets", "extract.gadgets")


def test_listed_jobs_limit_the_expansion(home):
    write_pipeline(home, _mutated(lambda c: c["steps"][0].update(jobs=["gadgets"])))
    assert [t.id for t in plan(load_pipeline("demo_daily"))] == ["extract.gadgets", "transform"]


def test_every_task_runs_through_the_same_command(home):
    for task in plan(load_pipeline("demo_daily")):
        assert task.argv == ("pipeline", "run-task", "demo_daily", task.id)


def test_tasks_are_topologically_ordered(home):
    config = copy.deepcopy(PIPELINE)
    config["steps"].reverse()  # declared out of order
    write_pipeline(home, config)
    ids = [t.id for t in plan(load_pipeline("demo_daily"))]
    assert ids.index("transform") > ids.index("extract.widgets")


def test_the_graph_is_versioned_json_for_adapters(home):
    data = graph(load_pipeline("demo_daily"))
    assert json.loads(json.dumps(data)) == data
    assert data["version"] == GRAPH_VERSION == 1
    assert data["pipeline"] == "demo_daily" and data["schedule"] == "0 6 * * *"
    assert data["tasks"][2] == {
        "id": "transform",
        "kind": "dbt",
        "depends_on": ["extract.widgets", "extract.gadgets"],
        "argv": ["pipeline", "run-task", "demo_daily", "transform"],
    }


def test_the_utilization_pipeline_extracts_every_netsuite_job_then_builds(monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("ELT_HOME", str(Path(__file__).parents[1]))
    tasks = plan(load_pipeline("utilization_daily"))
    extracts = [t for t in tasks if t.kind == "extract_load"]
    assert len(extracts) == 10 and {t.consumer for t in extracts} == {
        "vandelay_netsuite_extract_and_load"
    }
    (build,) = [t for t in tasks if t.kind == "dbt"]
    assert set(build.depends_on) == {t.id for t in extracts}
    assert tasks[-1] is build


VENDOR_CONSUMERS = {
    "globex_shopfront_extract_and_load",
    "umbrella_cartwheel_extract_and_load",
    "initech_ticketdesk_extract_and_load",
    "hooli_helpline_extract_and_load",
}


def test_the_vendor_pipeline_extracts_all_four_vendors_then_builds_their_models(monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("ELT_HOME", str(Path(__file__).parents[1]))
    pipeline = load_pipeline("vendor_normalization_daily")
    tasks = plan(pipeline)
    extracts = [t for t in tasks if t.kind == "extract_load"]
    assert {t.consumer for t in extracts} == VENDOR_CONSUMERS
    assert len(extracts) == 9  # shopfront 2, cartwheel 3, ticketdesk 2, helpline 2
    (step,) = [s for s in pipeline.steps if s.type == "dbt"]
    assert step.command == "build" and step.select == "tag:vendors+"
    (build,) = [t for t in tasks if t.kind == "dbt"]
    assert set(build.depends_on) == {t.id for t in extracts}


def test_the_utilization_pipeline_leaves_vendor_models_out(monkeypatch):
    """Its environment has no vendor extracts, so building vendor models there would fail."""
    from pathlib import Path

    monkeypatch.setenv("ELT_HOME", str(Path(__file__).parents[1]))
    (step,) = [s for s in load_pipeline("utilization_daily").steps if s.type == "dbt"]
    assert step.exclude == "tag:vendors"


# --- running tasks ---------------------------------------------------------------------


def test_an_extract_task_runs_its_job(home, calls):
    log, _ = calls
    result = run_task(load_pipeline("demo_daily"), "extract.gadgets", full_refresh=True)
    assert result.status == "succeeded" and result.detail["records_loaded"] == 3
    assert log == [("extract", "demo_fake_extract_and_load", "gadgets", True)]


def test_a_dbt_task_invokes_dbt_with_its_selection_under_a_pipeline_lock(home, calls):
    log, _ = calls
    write_pipeline(home, _mutated(lambda c: c["steps"][1].update(select="+reporting+")))
    result = run_task(load_pipeline("demo_daily"), "transform")
    assert result.status == "succeeded" and result.detail == {"pass": 5}
    assert log == [("dbt", ("build", "--select", "+reporting+"), "demo_daily")]


def test_failures_are_reported_not_raised(home, calls):
    _, outcomes = calls
    outcomes["widgets"] = SourceError("NetSuite SuiteQL request failed: 401 Unauthorized")
    outcomes["dbt"] = DbtOutcome(success=False, summary={"error": 1}, failures=["model x: boom"])
    pipeline = load_pipeline("demo_daily")
    extract = run_task(pipeline, "extract.widgets")
    assert extract.status == "failed" and "401 Unauthorized" in extract.error
    transform = run_task(pipeline, "transform")
    assert transform.status == "failed" and "model x: boom" in transform.error


def test_an_unknown_task_lists_the_planned_ones(home, calls):
    with pytest.raises(ConfigError, match=r"no task 'extract\.sprockets'.*extract\.widgets"):
        run_task(load_pipeline("demo_daily"), "extract.sprockets")


def test_run_pipeline_runs_in_order_and_skips_dependents_of_failures(home, calls):
    log, outcomes = calls
    results = run_pipeline(load_pipeline("demo_daily"))
    assert [r.status for r in results] == ["succeeded", "succeeded", "succeeded"]
    assert [entry[0] for entry in log] == ["extract", "extract", "dbt"]

    log.clear()
    outcomes["widgets"] = SourceError("boom")
    results = {r.task: r for r in run_pipeline(load_pipeline("demo_daily"))}
    assert results["extract.widgets"].status == "failed"
    assert results["extract.gadgets"].status == "succeeded"  # independent work still runs
    assert results["transform"].status == "skipped"
    assert "extract.widgets" in results["transform"].error
    assert [entry[0] for entry in log] == ["extract", "extract"]


# --- the dbt connection ----------------------------------------------------------------


def test_connection_env_follows_libpq_precedence():
    env = connection_env(
        "postgresql://elt:secret@db.internal:5433/warehouse?sslmode=require",
        {"PGHOST": "ignored", "PGPASSWORD": "ignored"},
    )
    assert env == {
        "DBT_PG_HOST": "db.internal",
        "DBT_PG_PORT": "5433",
        "DBT_PG_USER": "elt",
        "DBT_PG_PASSWORD": "secret",
        "DBT_PG_DBNAME": "warehouse",
        "DBT_PG_SSLMODE": "require",
    }


def test_connection_env_fills_gaps_from_pg_environment_variables():
    """On ECS the DSN names only the database; RDS credentials arrive as PG* variables."""
    env = connection_env(
        "postgresql:///warehouse?sslmode=require",
        {"PGHOST": "rds.example", "PGPORT": "5432", "PGUSER": "elt", "PGPASSWORD": "pw"},
    )
    assert env["DBT_PG_HOST"] == "rds.example" and env["DBT_PG_USER"] == "elt"
    assert env["DBT_PG_PASSWORD"] == "pw" and env["DBT_PG_DBNAME"] == "warehouse"
    assert env["DBT_PG_SSLMODE"] == "require"


def test_connection_env_defaults():
    env = connection_env("postgresql://elt@localhost/warehouse", {})
    assert env["DBT_PG_PORT"] == "5432" and env["DBT_PG_SSLMODE"] == "prefer"
    assert env["DBT_PG_PASSWORD"] == ""


# --- CLI ---------------------------------------------------------------------------------


def test_cli_lists_and_shows_pipelines(home):
    runner = CliRunner()
    listed = runner.invoke(app, ["pipeline", "list"])
    assert listed.exit_code == 0 and "demo_daily" in listed.output
    shown = runner.invoke(app, ["pipeline", "show", "demo_daily", "--json"])
    assert shown.exit_code == 0
    assert json.loads(shown.output) == graph(load_pipeline("demo_daily"))


def test_cli_exports_every_graph_for_adapters(home, tmp_path):
    out = tmp_path / "graphs"
    result = CliRunner().invoke(app, ["pipeline", "export", str(out)])
    assert result.exit_code == 0
    exported = json.loads((out / "demo_daily.json").read_text())
    assert exported == graph(load_pipeline("demo_daily"))


def test_cli_run_task_exit_codes_and_json(home, calls):
    _, outcomes = calls
    runner = CliRunner()
    ok = runner.invoke(app, ["pipeline", "run-task", "demo_daily", "extract.widgets", "--json"])
    assert ok.exit_code == 0
    assert json.loads(ok.output.strip().splitlines()[-1])["status"] == "succeeded"
    outcomes["widgets"] = SourceError("boom")
    failed = runner.invoke(app, ["pipeline", "run-task", "demo_daily", "extract.widgets"])
    assert failed.exit_code == 1 and "boom" in failed.output
    unknown = runner.invoke(app, ["pipeline", "run-task", "demo_daily", "nope"])
    assert unknown.exit_code == 2


def test_cli_run_exits_non_zero_when_any_task_fails(home, calls):
    _, outcomes = calls
    runner = CliRunner()
    assert runner.invoke(app, ["pipeline", "run", "demo_daily"]).exit_code == 0
    outcomes["dbt"] = DbtOutcome(success=False, summary={"error": 1}, failures=["boom"])
    result = runner.invoke(app, ["pipeline", "run", "demo_daily"])
    assert result.exit_code == 1 and "transform" in result.output
