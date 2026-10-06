# Airflow: a self-hosted Airflow service on Fargate that orchestrates elt-suite
# pipelines by starting the runner's task definition once per pipeline task.

mock_provider "aws" {
  mock_data "aws_region" {
    defaults = {
      region = "us-east-1"
    }
  }
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
  # The provider validates ARN syntax even for mocked values.
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/elt-test-airflow"
    }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = {
      arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:elt-test/airflow-abc"
    }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:us-east-1:123456789012:log-group:/ecs/elt-test-airflow"
    }
  }
  mock_resource "aws_ecs_task_definition" {
    defaults = {
      arn                  = "arn:aws:ecs:us-east-1:123456789012:task-definition/elt-test-airflow:1"
      arn_without_revision = "arn:aws:ecs:us-east-1:123456789012:task-definition/elt-test-airflow"
    }
  }
}

# The real random provider runs here (it needs no credentials): provider mocks
# don't support the ephemeral random_password resources yet.

variables {
  name              = "elt-test-airflow"
  repository_url    = "123456789012.dkr.ecr.us-east-1.amazonaws.com/elt-suite-airflow"
  image_tag         = "0123abc"
  subnet_ids        = ["subnet-aaa", "subnet-bbb"]
  security_group_id = "sg-task"
  database = {
    host              = "db.internal"
    port              = 5432
    master_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds!db-abc"
  }
  elt = {
    cluster_arn                     = "arn:aws:ecs:us-east-1:123456789012:cluster/elt-test"
    cluster_name                    = "elt-test"
    task_definition_family          = "elt-test"
    task_definition_arn_no_revision = "arn:aws:ecs:us-east-1:123456789012:task-definition/elt-test"
    container                       = "elt"
    execution_role_arn              = "arn:aws:iam::123456789012:role/elt-test-task-execution"
    task_role_arn                   = "arn:aws:iam::123456789012:role/elt-test-task"
    log_group_name                  = "/ecs/elt-test"
    log_group_arn                   = "arn:aws:logs:us-east-1:123456789012:log-group:/ecs/elt-test"
  }
}

# --- the service -------------------------------------------------------------------------

run "one_spot_service_with_execute_command_and_no_load_balancer" {
  command = apply

  assert {
    condition     = aws_ecs_service.this.desired_count == 1
    error_message = "LocalExecutor runs one scheduler; the service runs one task."
  }
  assert {
    condition     = one(aws_ecs_service.this.capacity_provider_strategy).capacity_provider == "FARGATE_SPOT"
    error_message = "Airflow is restartable; run it on Fargate Spot by default to cut cost."
  }
  assert {
    condition     = aws_ecs_service.this.enable_execute_command
    error_message = "ECS Exec is how operators reach the UI (SSM port forwarding)."
  }
  assert {
    condition     = length(aws_ecs_service.this.load_balancer) == 0
    error_message = "No load balancer: nothing is exposed to the internet."
  }
  assert {
    condition = (
      one(aws_ecs_service.this.network_configuration).assign_public_ip &&
      one(aws_ecs_service.this.network_configuration).security_groups == toset(["sg-task"])
    )
    error_message = "Without NAT the service needs a public IP; the task SG allows no inbound."
  }
  assert {
    condition     = aws_ecs_service.this.deployment_maximum_percent == 100 && aws_ecs_service.this.deployment_minimum_healthy_percent == 0
    error_message = "Deployments must stop the old scheduler before starting a new one."
  }
}

run "desired_count_zero_pauses_airflow" {
  command = apply

  variables {
    desired_count     = 0
    capacity_provider = "FARGATE"
  }

  assert {
    condition     = aws_ecs_service.this.desired_count == 0
    error_message = "desired_count = 0 must pause the service."
  }
  assert {
    condition     = one(aws_ecs_service.this.capacity_provider_strategy).capacity_provider == "FARGATE"
    error_message = "On-demand Fargate must be selectable."
  }
}

# --- containers --------------------------------------------------------------------------

run "init_runs_first_then_the_four_airflow_components" {
  command = apply

  assert {
    condition = toset([for c in jsondecode(aws_ecs_task_definition.this.container_definitions) : c.name]) == toset(
      ["init", "api-server", "scheduler", "dag-processor", "triggerer"]
    )
    error_message = "Expected init plus api-server, scheduler, dag-processor and triggerer."
  }
  assert {
    condition = alltrue([
      for c in jsondecode(aws_ecs_task_definition.this.container_definitions) :
      c.essential == (c.name != "init")
    ])
    error_message = "init exits after bootstrapping, so it must be the only non-essential container."
  }
  assert {
    condition = alltrue([
      for c in jsondecode(aws_ecs_task_definition.this.container_definitions) :
      c.name == "init" || contains(c.dependsOn, { containerName = "init", condition = "SUCCESS" })
    ])
    error_message = "Airflow components start only after init succeeded."
  }
  assert {
    condition = (
      [for c in jsondecode(aws_ecs_task_definition.this.container_definitions) : c.command if c.name == "scheduler"][0] == ["scheduler"] &&
      [for c in jsondecode(aws_ecs_task_definition.this.container_definitions) : c.command if c.name == "api-server"][0] == ["api-server", "--port", "8080"]
    )
    error_message = "Each container runs one Airflow component."
  }
  assert {
    condition = alltrue([
      for c in jsondecode(aws_ecs_task_definition.this.container_definitions) :
      c.image == "123456789012.dkr.ecr.us-east-1.amazonaws.com/elt-suite-airflow:0123abc"
    ])
    error_message = "Every container runs the Airflow image CI pushed for this commit."
  }
  assert {
    condition     = aws_ecs_task_definition.this.cpu == "1024" && aws_ecs_task_definition.this.memory == "4096"
    error_message = "Four Airflow processes need 1 vCPU and 4 GB."
  }
}

run "airflow_is_configured_to_run_tasks_on_ecs" {
  command = apply

  assert {
    condition = alltrue([
      for pair in [
        ["ELT_RUNNER", "ecs"],
        ["ELT_ECS_CLUSTER", "elt-test"],
        ["ELT_ECS_TASK_DEFINITION", "elt-test"],
        ["ELT_ECS_SUBNETS", "subnet-aaa,subnet-bbb"],
        ["ELT_ECS_SECURITY_GROUPS", "sg-task"],
        ["ELT_ECS_LOG_GROUP", "/ecs/elt-test"],
        ["AWS_DEFAULT_REGION", "us-east-1"],
        ["AIRFLOW__CORE__EXECUTOR", "LocalExecutor"],
        ["AIRFLOW__CORE__EXECUTION_API_SERVER_URL", "http://localhost:8080/execution/"],
        ["AIRFLOW_DB_SSLMODE", "require"],
      ] :
      contains(jsondecode(aws_ecs_task_definition.this.container_definitions)[1].environment, { name = pair[0], value = pair[1] })
    ])
    error_message = "Airflow must start pipeline tasks on the elt cluster and reach its own API locally."
  }
}

# --- secrets -----------------------------------------------------------------------------

run "generated_secrets_never_enter_state" {
  command = apply

  assert {
    condition     = aws_secretsmanager_secret_version.this.secret_string == null
    error_message = "Write the generated secrets with secret_string_wo so they stay out of state."
  }
  assert {
    condition = alltrue([
      for name in ["AIRFLOW__CORE__FERNET_KEY", "AIRFLOW__API_AUTH__JWT_SECRET", "AIRFLOW__API__SECRET_KEY", "AIRFLOW_DB_PASSWORD"] :
      contains(
        [for s in jsondecode(aws_ecs_task_definition.this.container_definitions)[1].secrets : s.name],
        name
      )
    ])
    error_message = "Every component shares the Fernet key, JWT secret, API secret and DB password."
  }
}

run "only_init_sees_the_rds_master_credentials" {
  command = apply

  assert {
    condition = alltrue([
      for c in jsondecode(aws_ecs_task_definition.this.container_definitions) :
      anytrue([for s in c.secrets : startswith(s.valueFrom, "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds!db-abc")]) == (c.name == "init")
    ])
    error_message = "Only init uses the master login, to create Airflow's own role and database."
  }
}

# --- IAM ---------------------------------------------------------------------------------

run "airflow_can_only_run_the_elt_task_in_its_cluster" {
  command = apply

  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.task.policy).Statement :
      s.Action == "ecs:RunTask" &&
      s.Resource == "arn:aws:ecs:us-east-1:123456789012:task-definition/elt-test:*" &&
      s.Condition.ArnEquals["ecs:cluster"] == "arn:aws:ecs:us-east-1:123456789012:cluster/elt-test"
    ])
    error_message = "RunTask is limited to the elt task definition in the elt cluster."
  }
  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.task.policy).Statement :
      s.Action == "iam:PassRole" && toset(flatten([s.Resource])) == toset([
        "arn:aws:iam::123456789012:role/elt-test-task-execution",
        "arn:aws:iam::123456789012:role/elt-test-task",
      ])
    ])
    error_message = "Airflow may pass only the elt task's own roles."
  }
  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.task.policy).Statement :
      toset(flatten([s.Action])) == toset(["ecs:DescribeTasks", "ecs:StopTask"]) &&
      s.Resource == "arn:aws:ecs:us-east-1:123456789012:task/elt-test/*"
    ])
    error_message = "Airflow may watch and stop only tasks in the elt cluster."
  }
  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.task.policy).Statement :
      s.Action == "logs:GetLogEvents" &&
      s.Resource == "arn:aws:logs:us-east-1:123456789012:log-group:/ecs/elt-test:log-stream:*"
    ])
    error_message = "Airflow streams the elt task's logs, nothing else."
  }
  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.task.policy).Statement :
      contains(flatten([s.Action]), "ssmmessages:OpenDataChannel")
    ])
    error_message = "ECS Exec needs the SSM message channels."
  }
}

run "execution_role_reads_only_airflow_and_database_secrets" {
  command = apply

  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.execution_secrets.policy).Statement :
      s.Action == "secretsmanager:GetSecretValue" && toset(flatten([s.Resource])) == toset([
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:elt-test/airflow-abc",
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds!db-abc",
      ])
    ])
    error_message = "The execution role reads only Airflow's secret and the database secret."
  }
}

run "logs_are_kept_for_two_weeks" {
  command = apply

  assert {
    condition     = aws_cloudwatch_log_group.this.retention_in_days == 14 && aws_cloudwatch_log_group.task_logs.retention_in_days == 14
    error_message = "Airflow and task logs must not be kept forever."
  }
}
