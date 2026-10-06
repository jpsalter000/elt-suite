# 3. Self-host Airflow on ECS Fargate instead of MWAA

- Status: accepted
- Date: 2026-10-05

## Context

The utilization pipeline is orchestrated by Airflow. AWS offers two ways to run Airflow:

- **Amazon MWAA:** about $250–$400 a month for the smallest environment, before NAT. It also requires private subnets with a NAT Gateway or VPC endpoints, which contradicts ADR 0002.
- **Self-hosting on ECS:** Airflow 3 is four processes (api-server, scheduler, dag-processor and triggerer) plus a metadata database. At this scale they fit in one Fargate task with LocalExecutor, and the warehouse's RDS instance can host the metadata database.

The pipeline work itself runs in the existing elt task definition, not inside Airflow. dbt and Airflow pin incompatible libraries (protobuf), so they can't share a Python environment anyway.

## Decision

- **Service:** run Airflow as one ECS service, `modules/airflow`, with desired count 1 on **Fargate Spot**. It is 1 vCPU and 4 GB, with all four components in one task sharing localhost.
- **Tasks:** every DAG task starts the elt task definition with `EcsRunTaskOperator` and the command `pipeline run-task <pipeline> <task>`. Airflow's role may run, describe and stop only that task definition in that cluster, and may pass only its roles.
- **Metadata database:** a dedicated `airflow` role and database on the warehouse instance. A non-essential init container creates them idempotently, using the RDS master secret. Airflow never logs in as the master user, so the master secret's automatic rotation can't break it.
- **Secrets:** the Fernet key, JWT secret, API secret, database password and admin password are generated as ephemeral values. They are written to Secrets Manager with `secret_string_wo`, so they never enter Terraform state (ADR 0001).
- **Access:** no load balancer, and no inbound rules. Operators reach the UI through ECS Exec port forwarding (see `docs/orchestration.md`).
- **Fallback:** the runner's EventBridge schedule stays available for running without Airflow. Dev turns it off whenever Airflow is on, so a pipeline never runs twice.

## Consequences

- **Cost:** about $13 a month on Spot (about $43 on demand, for 1 vCPU and 4 GB in us-east-1) for Airflow, against MWAA's $250 or more plus NAT. Setting `airflow_desired_count = 0` pauses it.
- **Spot interruptions:** these restart the service. A DAG run that was in progress resumes or retries according to Airflow's own state, and the elt tasks it started keep running to completion on their own.
- **Scaling:** LocalExecutor limits concurrency to what one task can supervise. The work runs in separate Fargate tasks, so that limit is generous. The next step up would be CeleryExecutor or the ECS executor, not a bigger task.
- **Operations:** we operate Airflow upgrades ourselves. The image pins an Airflow version and installs it with Airflow's constraints file.
- **Portability:** orchestration stays a thin adapter (`orchestration/airflow`). Moving to MWAA, or to another orchestrator, reuses the same `elt pipeline run-task` contract.
