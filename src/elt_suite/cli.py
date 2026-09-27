"""Command-line entry point: ``elt list | infer | run``."""

from __future__ import annotations

import logging
from typing import Annotated

import typer
from dotenv import load_dotenv

from elt_suite.config import ConfigError, ConsumerConfig, JobConfig, list_consumers, load_consumer
from elt_suite.inference import InferenceError, run_inference
from elt_suite.load import LoadError, run_full

app = typer.Typer(no_args_is_help=True, add_completion=False)

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
        except (ConfigError, InferenceError) as exc:
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
        except (ConfigError, InferenceError, LoadError) as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(1) from exc
        typer.secho(
            f"{cfg.name}.{j.name}: loaded {result.records_loaded} records (run {result.run_id})",
            fg="green",
        )


if __name__ == "__main__":
    app()
