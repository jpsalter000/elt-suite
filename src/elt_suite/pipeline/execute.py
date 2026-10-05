"""Run pipeline tasks: one at a time (for orchestrators) or all in order (built in)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from elt_suite.config import ConfigError, load_consumer
from elt_suite.load import run_full
from elt_suite.pipeline.config import DbtStep, PipelineConfig
from elt_suite.pipeline.dbt import invoke_dbt
from elt_suite.pipeline.plan import Task, plan

log = logging.getLogger("elt_suite.pipeline")

Status = Literal["succeeded", "failed", "skipped"]


@dataclass
class TaskResult:
    pipeline: str
    task: str
    status: Status
    started_at: str
    finished_at: str
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _find(pipeline: PipelineConfig, task_id: str) -> Task:
    tasks = plan(pipeline)
    for task in tasks:
        if task.id == task_id:
            return task
    raise ConfigError(
        f"pipeline {pipeline.name!r} has no task {task_id!r}; planned: {[t.id for t in tasks]}"
    )


def _run(pipeline: PipelineConfig, task: Task, full_refresh: bool) -> dict[str, Any]:
    if task.kind == "extract_load":
        assert task.consumer and task.job
        consumer = load_consumer(task.consumer)
        result = run_full(consumer, consumer.job(task.job), full_refresh=full_refresh)
        return {
            "run_id": str(result.run_id),
            "records_loaded": result.records_loaded,
            "max_incremental_value": result.max_incremental_value,
        }
    step = pipeline.step(task.step)
    assert isinstance(step, DbtStep)
    args = [step.command]
    if step.select:
        args += ["--select", step.select]
    if step.exclude:
        args += ["--exclude", step.exclude]
    outcome = invoke_dbt(args, lock_key=pipeline.name)
    if not outcome.success:
        raise RuntimeError("dbt failed: " + ("; ".join(outcome.failures) or str(outcome.summary)))
    return dict(outcome.summary)


def run_task(pipeline: PipelineConfig, task_id: str, *, full_refresh: bool = False) -> TaskResult:
    """Run one task. Failures are captured in the result, not raised.

    An unknown task id is a usage error and raises ``ConfigError``.
    """
    task = _find(pipeline, task_id)
    started = _now()
    log.info("%s.%s: starting", pipeline.name, task.id)
    try:
        detail = _run(pipeline, task, full_refresh)
    except Exception as exc:  # report every failure to the orchestrator
        log.error("%s.%s: failed: %s", pipeline.name, task.id, exc)
        return TaskResult(pipeline.name, task.id, "failed", started, _now(), error=str(exc))
    log.info("%s.%s: succeeded %s", pipeline.name, task.id, detail)
    return TaskResult(pipeline.name, task.id, "succeeded", started, _now(), detail)


def run_pipeline(
    pipeline: PipelineConfig,
    *,
    full_refresh: bool = False,
    on_result: Callable[[TaskResult], None] | None = None,
) -> list[TaskResult]:
    """The built-in orchestrator: every task in dependency order, in this process.

    A failed task skips its dependents (transitively). Independent tasks still run.
    """
    results: list[TaskResult] = []
    unhealthy: dict[str, str] = {}  # task id -> the failed task that blocks it
    for task in plan(pipeline):
        blockers = sorted({unhealthy[d] for d in task.depends_on if d in unhealthy})
        if blockers:
            now = _now()
            result = TaskResult(
                pipeline.name, task.id, "skipped", now, now,
                error=f"skipped because {', '.join(blockers)} failed",
            )  # fmt: skip
            unhealthy[task.id] = blockers[0]
        else:
            result = run_task(pipeline, task.id, full_refresh=full_refresh)
            if result.status != "succeeded":
                unhealthy[task.id] = task.id
        results.append(result)
        if on_result:
            on_result(result)
    return results
