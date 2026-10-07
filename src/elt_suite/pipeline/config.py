"""Pipeline configuration: a small DAG of extract-and-load and dbt steps.

``config/pipelines/<name>.json``::

    {
      "name": "utilization_daily",
      "schedule": "0 6 * * *",
      "steps": [
        {"id": "extract", "type": "extract_load", "consumer": "vandelay_netsuite_extract_and_load"},
        {"id": "transform", "type": "dbt", "command": "build", "depends_on": ["extract"]}
      ]
    }

``schedule`` is advisory: orchestrator adapters use it, but the core never schedules.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from elt_suite import paths
from elt_suite.config import (
    IDENTIFIER,
    ConfigError,
    _read_json,
    _Strict,
    list_consumers,
    load_consumer,
)


class _Step(_Strict):
    id: str = Field(pattern=IDENTIFIER)
    depends_on: list[str] = Field(default_factory=list)


class ExtractLoadStep(_Step):
    """Extract-and-load a consumer's jobs (all of them unless ``jobs`` lists some)."""

    type: Literal["extract_load"]
    consumer: str
    jobs: list[str] | None = Field(default=None, min_length=1)


class DbtStep(_Step):
    """Run a dbt command against the project in ``transform/``."""

    type: Literal["dbt"]
    command: Literal["build", "run", "test", "seed"]
    select: str | None = None
    exclude: str | None = None

    @model_validator(mode="after")
    def _selection_includes_descendants(self) -> DbtStep:
        # dbt replaces a view by renaming the old one and dropping it with CASCADE,
        # which also drops every view built on top of it. A partial selection would
        # leave those dependents missing, so each selector must take its descendants.
        if self.select:
            partial = [s for s in self.select.split() if not s.endswith("+")]
            if partial:
                raise ValueError(
                    f"select {partial} must include descendants (end each selector with '+'): "
                    "rebuilding a view drops the views built on it"
                )
        return self


Step = Annotated[ExtractLoadStep | DbtStep, Field(discriminator="type")]


class PipelineConfig(_Strict):
    name: str = Field(pattern=IDENTIFIER)
    description: str = ""
    schedule: str | None = Field(default=None, description="Advisory cron expression (UTC).")
    steps: list[Step] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid_graph(self) -> PipelineConfig:
        ids = [s.id for s in self.steps]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate step ids: {dupes}")
        for step in self.steps:
            unknown = [d for d in step.depends_on if d not in ids]
            if unknown:
                raise ValueError(f"step {step.id!r} depends on unknown step(s) {unknown}")
        deps = {s.id: s.depends_on for s in self.steps}
        visiting: set[str] = set()
        done: set[str] = set()

        def visit(node: str, trail: list[str]) -> None:
            if node in done:
                return
            if node in visiting:
                cycle = " -> ".join([*trail[trail.index(node) :], node])
                raise ValueError(f"steps form a cycle: {cycle}")
            visiting.add(node)
            for dep in deps[node]:
                visit(dep, [*trail, node])
            visiting.discard(node)
            done.add(node)

        for step_id in ids:
            visit(step_id, [])
        return self

    def step(self, step_id: str) -> ExtractLoadStep | DbtStep:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise ConfigError(f"pipeline {self.name!r} has no step {step_id!r}")


def list_pipelines() -> list[str]:
    folder = paths.pipelines_dir()
    return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []


def load_pipeline(name: str) -> PipelineConfig:
    path = paths.pipelines_dir() / f"{name}.json"
    if not path.exists():
        raise ConfigError(f"no pipeline {name!r} in {path.parent}; configured: {list_pipelines()}")
    try:
        pipeline = PipelineConfig.model_validate(_read_json(path))
    except ValueError as exc:  # pydantic.ValidationError subclasses ValueError
        raise ConfigError(f"invalid pipeline config {path}:\n{exc}") from exc
    if pipeline.name != name:
        raise ConfigError(
            f"{path.name} describes pipeline {pipeline.name!r}; rename the file to "
            f"{pipeline.name}.json"
        )
    for step in pipeline.steps:
        if isinstance(step, ExtractLoadStep):
            if step.consumer not in list_consumers():
                raise ConfigError(
                    f"pipeline {name!r} step {step.id!r}: unknown consumer {step.consumer!r}; "
                    f"configured: {list_consumers()}"
                )
            consumer = load_consumer(step.consumer)
            for job in step.jobs or []:
                consumer.job(job)  # raises ConfigError listing the configured jobs
    return pipeline
