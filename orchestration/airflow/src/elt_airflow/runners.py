"""How an Airflow task runs one elt-suite task: wherever ``elt <argv>`` can run."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from elt_airflow.graphs import GraphError


class Runner(Protocol):
    def operator(self, task: dict[str, Any]) -> Any:
        """Build the Airflow operator that runs ``elt <task['argv']>``."""


@dataclass(frozen=True)
class EcsRunner:
    """Run each task as a one-off Fargate task of the elt image (its entrypoint is ``elt``)."""

    cluster: str
    task_definition: str
    subnets: list[str]
    security_groups: list[str]
    region: str
    log_group: str
    container: str = "elt"
    log_stream_prefix: str = "elt"
    # Without a NAT gateway (ADR 0002) tasks reach ECR, Secrets Manager and CloudWatch
    # through the internet gateway, which needs a public IP.
    assign_public_ip: bool = True
    deferrable: bool = True

    def operator(self, task: dict[str, Any]) -> Any:
        from airflow.providers.amazon.aws.operators.ecs import EcsRunTaskOperator

        return EcsRunTaskOperator(
            task_id=task["id"],
            cluster=self.cluster,
            task_definition=self.task_definition,
            launch_type="FARGATE",
            overrides={"containerOverrides": [{"name": self.container, "command": task["argv"]}]},
            network_configuration={
                "awsvpcConfiguration": {
                    "subnets": self.subnets,
                    "securityGroups": self.security_groups,
                    "assignPublicIp": "ENABLED" if self.assign_public_ip else "DISABLED",
                }
            },
            region_name=self.region,
            awslogs_group=self.log_group,
            awslogs_region=self.region,
            # ECS names streams <prefix>/<container>/<task id>.
            awslogs_stream_prefix=f"{self.log_stream_prefix}/{self.container}",
            deferrable=self.deferrable,
            waiter_delay=15,
            waiter_max_attempts=480,  # two hours
        )


@dataclass(frozen=True)
class LocalRunner:
    """Run each task with an elt CLI installed next to Airflow (docker compose, dev)."""

    elt: str = "/opt/elt/.venv/bin/elt"
    env: dict[str, str] = field(default_factory=dict)

    def operator(self, task: dict[str, Any]) -> Any:
        from airflow.providers.standard.operators.bash import BashOperator

        return BashOperator(
            task_id=task["id"],
            bash_command=shlex.join([self.elt, *task["argv"]]),
            env=self.env or None,
            append_env=True,
        )


ECS_SETTINGS = {
    "cluster": "ELT_ECS_CLUSTER",
    "task_definition": "ELT_ECS_TASK_DEFINITION",
    "subnets": "ELT_ECS_SUBNETS",
    "security_groups": "ELT_ECS_SECURITY_GROUPS",
    "log_group": "ELT_ECS_LOG_GROUP",
    "region": "AWS_DEFAULT_REGION",
}


def runner_from_env(environ: Mapping[str, str]) -> Runner:
    """``ELT_RUNNER=ecs`` (with ``ELT_ECS_*``) or ``ELT_RUNNER=local`` (``ELT_BIN``)."""
    kind = environ.get("ELT_RUNNER", "local")
    if kind == "local":
        return LocalRunner(elt=environ.get("ELT_BIN", LocalRunner.elt))
    if kind != "ecs":
        raise GraphError(f"ELT_RUNNER must be 'ecs' or 'local'; got {kind!r}")
    missing = [var for var in ECS_SETTINGS.values() if not environ.get(var)]
    if missing:
        raise GraphError(f"ELT_RUNNER=ecs needs {', '.join(missing)}")
    values = {name: environ[var] for name, var in ECS_SETTINGS.items()}
    return EcsRunner(
        cluster=values["cluster"],
        task_definition=values["task_definition"],
        subnets=[s.strip() for s in values["subnets"].split(",") if s.strip()],
        security_groups=[s.strip() for s in values["security_groups"].split(",") if s.strip()],
        region=values["region"],
        log_group=values["log_group"],
        container=environ.get("ELT_ECS_CONTAINER", "elt"),
    )
