"""Expand a pipeline into runnable tasks, the contract every orchestrator wraps.

Each task can run on its own with ``elt pipeline run-task <pipeline> <task>`` (its
``argv``). Adapters read the versioned JSON from :func:`graph` and never need to
import elt_suite, so they can live in environments with incompatible dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from elt_suite.config import load_consumer
from elt_suite.pipeline.config import ExtractLoadStep, PipelineConfig

GRAPH_VERSION = 1


@dataclass(frozen=True)
class Task:
    id: str
    step: str
    kind: Literal["extract_load", "dbt"]
    depends_on: tuple[str, ...]
    argv: tuple[str, ...]
    consumer: str | None = None
    job: str | None = None


def _step_tasks(pipeline: PipelineConfig) -> dict[str, list[tuple[str, str | None]]]:
    """Step id -> [(task id, job)], in declaration order."""
    expanded: dict[str, list[tuple[str, str | None]]] = {}
    for step in pipeline.steps:
        if isinstance(step, ExtractLoadStep):
            jobs = step.jobs or [j.name for j in load_consumer(step.consumer).jobs]
            expanded[step.id] = [(f"{step.id}.{job}", job) for job in jobs]
        else:
            expanded[step.id] = [(step.id, None)]
    return expanded


def plan(pipeline: PipelineConfig) -> list[Task]:
    """Tasks in a topological order that keeps declaration order where it can."""
    expanded = _step_tasks(pipeline)
    remaining = list(pipeline.steps)
    ordered: list[Task] = []
    placed: set[str] = set()
    while remaining:
        step = next(s for s in remaining if set(s.depends_on) <= placed)  # config is acyclic
        remaining.remove(step)
        placed.add(step.id)
        upstream = tuple(task_id for dep in step.depends_on for task_id, _ in expanded[dep])
        for task_id, job in expanded[step.id]:
            ordered.append(
                Task(
                    id=task_id,
                    step=step.id,
                    kind=step.type,
                    depends_on=upstream,
                    argv=("pipeline", "run-task", pipeline.name, task_id),
                    consumer=step.consumer if isinstance(step, ExtractLoadStep) else None,
                    job=job,
                )
            )
    return ordered


def graph(pipeline: PipelineConfig) -> dict[str, Any]:
    """The JSON document orchestrator adapters consume."""
    return {
        "version": GRAPH_VERSION,
        "pipeline": pipeline.name,
        "description": pipeline.description,
        "schedule": pipeline.schedule,
        "tasks": [
            {
                "id": t.id,
                "kind": t.kind,
                "depends_on": list(t.depends_on),
                "argv": list(t.argv),
            }
            for t in plan(pipeline)
        ],
    }
