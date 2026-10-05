"""Command-line entry point: ``elt list | infer | run | pipeline ...``."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

from elt_suite.config import ConfigError, ConsumerConfig, JobConfig, list_consumers, load_consumer
from elt_suite.contract import SourceError
from elt_suite.inference import InferenceError, run_inference
from elt_suite.load import LoadError, run_full
from elt_suite.pipeline import (
    PipelineConfig,
    TaskResult,
    graph,
    list_pipelines,
    load_pipeline,
    plan,
    run_pipeline,
    run_task,
)

app = typer.Typer(no_args_is_help=True, add_completion=False)
pipeline_app = typer.Typer(
    no_args_is_help=True, help="Run pipelines: extract-and-load jobs chained with dbt."
)
app.add_typer(pipeline_app, name="pipeline")

JobOption = Annotated[
    list[str] | None, typer.Option("--job", "-j", help="Job name (repeatable). Default: all.")
]


@app.callback()
def main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    """Config-driven extract-and-load with schema inference."""
    load_dotenv()
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _selected(consumer_name: str, jobs: list[str] | None) -> tuple[ConsumerConfig, list[JobConfig]]:
    try:
        consumer = load_consumer(consumer_name)
        return consumer, [consumer.job(j) for j in jobs] if jobs else list(consumer.jobs)
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("list")
def list_cmd() -> None:
    """List configured consumers and their jobs."""
    for name in list_consumers():
        consumer = load_consumer(name)
        typer.echo(f"{name}  ->  {consumer.destination}:{consumer.target_schema}")
        for job in consumer.jobs:
            inc = job.incremental_loading
            suffix = f"  (incremental on {inc.incremental_key} >= {inc.lower_bound})" if inc else ""
            typer.echo(f"  - {job.name}  pk={job.primary_keys}{suffix}")


@app.command()
def infer(consumer: str, job: JobOption = None) -> None:
    """Run jobs to completion and write inferred schemas to schemas/<consumer>/<job>.json."""
    cfg, jobs = _selected(consumer, job)
    for j in jobs:
        try:
            path = run_inference(cfg, j)
        except (ConfigError, InferenceError, SourceError) as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(1) from exc
        typer.secho(f"{cfg.name}.{j.name}: schema written to {path}", fg="green")


@app.command()
def run(
    consumer: str,
    job: JobOption = None,
    full_refresh: Annotated[
        bool,
        typer.Option(
            "--full-refresh", help="Ignore saved state and read from the configured lower_bound."
        ),
    ] = False,
) -> None:
    """Extract-and-load jobs into the consumer's destination, resuming from saved state."""
    cfg, jobs = _selected(consumer, job)
    for j in jobs:
        try:
            result = run_full(cfg, j, full_refresh=full_refresh)
        except (ConfigError, InferenceError, LoadError, SourceError) as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(1) from exc
        typer.secho(
            f"{cfg.name}.{j.name}: loaded {result.records_loaded} records (run {result.run_id})",
            fg="green",
        )


# --- pipelines --------------------------------------------------------------------------

FullRefreshOption = Annotated[
    bool,
    typer.Option("--full-refresh", help="Extract tasks ignore saved state."),
]


def _pipeline(name: str) -> PipelineConfig:
    try:
        return load_pipeline(name)
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _report(result: TaskResult) -> None:
    if result.status == "succeeded":
        typer.secho(f"{result.task}: succeeded {result.detail}", fg="green")
    else:
        typer.secho(f"{result.task}: {result.status}: {result.error}", fg="red")


@pipeline_app.command("list")
def pipeline_list() -> None:
    """List pipelines and their tasks."""
    for name in list_pipelines():
        pipeline = load_pipeline(name)
        schedule = f"  (schedule: {pipeline.schedule})" if pipeline.schedule else ""
        typer.echo(f"{name}{schedule}")
        for task in plan(pipeline):
            after = f"  after {', '.join(task.depends_on)}" if task.depends_on else ""
            typer.echo(f"  - {task.id} [{task.kind}]{after}")


@pipeline_app.command("show")
def pipeline_show(
    name: str,
    as_json: Annotated[bool, typer.Option("--json", help="Print the adapter graph.")] = False,
) -> None:
    """Show a pipeline's task graph."""
    pipeline = _pipeline(name)
    if as_json:
        typer.echo(json.dumps(graph(pipeline), indent=2))
        return
    for task in plan(pipeline):
        typer.echo(f"{task.id} [{task.kind}] <- {list(task.depends_on)}: elt {' '.join(task.argv)}")


@pipeline_app.command("export")
def pipeline_export(directory: Path) -> None:
    """Write every pipeline's graph to DIRECTORY/<name>.json for orchestrator adapters."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in list_pipelines():
        path = directory / f"{name}.json"
        document = json.dumps(graph(load_pipeline(name)), indent=2)
        path.write_text(document + "\n", encoding="utf-8")
        typer.echo(f"wrote {path}")


@pipeline_app.command("run")
def pipeline_run(name: str, full_refresh: FullRefreshOption = False) -> None:
    """Run every task in dependency order; a failure skips only its dependents."""
    results = run_pipeline(_pipeline(name), full_refresh=full_refresh, on_result=_report)
    if any(r.status != "succeeded" for r in results):
        raise typer.Exit(1)


@pipeline_app.command("run-task")
def pipeline_run_task(
    name: str,
    task: str,
    full_refresh: FullRefreshOption = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
) -> None:
    """Run one task: the command every orchestrator adapter invokes."""
    pipeline = _pipeline(name)
    try:
        result = run_task(pipeline, task, full_refresh=full_refresh)
    except ConfigError as exc:
        raise typer.BadParameter(str(exc), param_hint="TASK") from exc
    _report(result)
    if as_json:
        typer.echo(json.dumps(result.to_json()))
    if result.status != "succeeded":
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
