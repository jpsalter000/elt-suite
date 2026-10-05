# Runner: ECS cluster + Fargate task definition that runs the elt CLI, with
# least-privilege roles and an optional EventBridge Scheduler trigger.

mock_provider "aws" {
  mock_data "aws_region" {
    defaults = {
      region = "us-east-1"
    }
  }
  # The provider validates ARN syntax even for mocked values.
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/elt-test"
    }
  }
  mock_resource "aws_ecs_cluster" {
    defaults = {
      arn = "arn:aws:ecs:us-east-1:123456789012:cluster/elt-test"
    }
  }
  mock_resource "aws_ecs_task_definition" {
    defaults = {
      arn                  = "arn:aws:ecs:us-east-1:123456789012:task-definition/elt-test:1"
      arn_without_revision = "arn:aws:ecs:us-east-1:123456789012:task-definition/elt-test"
    }
  }
}

variables {
  name              = "elt-test"
  repository_url    = "123456789012.dkr.ecr.us-east-1.amazonaws.com/elt-test"
  image_tag         = "0123abc"
  subnet_ids        = ["subnet-aaa", "subnet-bbb"]
  security_group_id = "sg-task"
  database = {
    host       = "db.internal"
    port       = 5432
    name       = "warehouse"
    secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds!db-abc"
  }
  consumer_secrets = {
    INITECH_TICKETDESK_API_KEY = "arn:aws:ssm:us-east-1:123456789012:parameter/elt-test/ticketdesk-api-key"
  }
}

run "fargate_task_runs_the_pinned_image" {
  command = apply

  assert {
    condition = (
      aws_ecs_task_definition.this.requires_compatibilities == toset(["FARGATE"]) &&
      aws_ecs_task_definition.this.network_mode == "awsvpc" &&
      aws_ecs_task_definition.this.cpu == "512" &&
      aws_ecs_task_definition.this.memory == "1024"
    )
    error_message = "dbt and a mock sidecar need 0.5 vCPU and 1 GB."
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.this.container_definitions)[0].image == "123456789012.dkr.ecr.us-east-1.amazonaws.com/elt-test:0123abc"
    error_message = "The container must run the image tagged with the deployed git SHA."
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.this.container_definitions)[0].command == ["run", "demo_usgs_extract_and_load"]
    error_message = "Default command should run the no-credentials demo consumer."
  }
}

run "container_is_hardened" {
  command = apply

  assert {
    condition     = jsondecode(aws_ecs_task_definition.this.container_definitions)[0].readonlyRootFilesystem
    error_message = "Root filesystem must be read-only."
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.this.container_definitions)[0].user == "10001"
    error_message = "Container must run as the non-root elt user."
  }
  assert {
    condition     = one([for m in jsondecode(aws_ecs_task_definition.this.container_definitions)[0].mountPoints : m.containerPath]) == "/tmp"
    error_message = "Only /tmp is writable, via an ephemeral volume."
  }
}

run "database_credentials_come_from_secrets_manager" {
  command = apply

  assert {
    condition = contains(
      jsondecode(aws_ecs_task_definition.this.container_definitions)[0].secrets,
      { name = "PGPASSWORD", valueFrom = "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds!db-abc:password::" }
    )
    error_message = "PGPASSWORD must be injected from the RDS-managed secret."
  }
  assert {
    condition = contains(
      jsondecode(aws_ecs_task_definition.this.container_definitions)[0].secrets,
      { name = "PGUSER", valueFrom = "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds!db-abc:username::" }
    )
    error_message = "PGUSER must be injected from the RDS-managed secret."
  }
  assert {
    condition = contains(
      jsondecode(aws_ecs_task_definition.this.container_definitions)[0].environment,
      { name = "WAREHOUSE_DSN", value = "postgresql:///warehouse?sslmode=require" }
    )
    error_message = "WAREHOUSE_DSN carries no credentials; libpq fills host/user/password from PG* variables."
  }
  assert {
    condition = contains(
      jsondecode(aws_ecs_task_definition.this.container_definitions)[0].environment,
      { name = "PGHOST", value = "db.internal" }
    )
    error_message = "PGHOST must point at the database."
  }
}

run "consumer_secrets_are_injected_from_ssm" {
  command = apply

  assert {
    condition = contains(
      jsondecode(aws_ecs_task_definition.this.container_definitions)[0].secrets,
      { name = "INITECH_TICKETDESK_API_KEY", valueFrom = "arn:aws:ssm:us-east-1:123456789012:parameter/elt-test/ticketdesk-api-key" }
    )
    error_message = "Consumer credentials must be injected from SSM Parameter Store."
  }
}

run "execution_role_reads_only_its_own_secrets" {
  command = apply

  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.execution_secrets.policy).Statement :
      s.Action == "secretsmanager:GetSecretValue" && s.Resource == "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds!db-abc"
    ])
    error_message = "Execution role may read only the database secret."
  }
  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.execution_secrets.policy).Statement :
      s.Action == "ssm:GetParameters" && s.Resource == ["arn:aws:ssm:us-east-1:123456789012:parameter/elt-test/ticketdesk-api-key"]
    ])
    error_message = "Execution role may read only the configured SSM parameters."
  }
  assert {
    condition     = aws_iam_role_policy_attachment.execution.policy_arn == "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
    error_message = "Execution role needs the managed ECS execution policy for ECR pulls and logs."
  }
}

run "logs_are_kept_for_two_weeks" {
  command = apply

  assert {
    condition     = aws_cloudwatch_log_group.this.retention_in_days == 14
    error_message = "Task logs must not be kept forever."
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.this.container_definitions)[0].logConfiguration.options["awslogs-group"] == aws_cloudwatch_log_group.this.name
    error_message = "The container must log to the managed log group."
  }
  assert {
    condition     = one([for s in aws_ecs_cluster.this.setting : s.value if s.name == "containerInsights"]) == "disabled"
    error_message = "Container Insights costs extra; keep it off for the demo."
  }
}

run "not_scheduled_by_default" {
  command = apply

  assert {
    condition     = length(aws_scheduler_schedule.this) == 0
    error_message = "No schedule unless schedule_expression is set."
  }
}

run "optional_schedule_runs_in_public_subnets_with_the_task_sg" {
  command = apply

  variables {
    schedule_expression = "cron(0 6 * * ? *)"
  }

  assert {
    condition     = length(aws_scheduler_schedule.this) == 1
    error_message = "Setting schedule_expression must create a schedule."
  }
  assert {
    condition = (
      aws_scheduler_schedule.this[0].target[0].ecs_parameters[0].launch_type == "FARGATE" &&
      aws_scheduler_schedule.this[0].target[0].ecs_parameters[0].network_configuration[0].assign_public_ip &&
      aws_scheduler_schedule.this[0].target[0].ecs_parameters[0].network_configuration[0].security_groups == toset(["sg-task"])
    )
    error_message = "Scheduled tasks need a public IP (no NAT) and the no-inbound task security group."
  }
}

run "the_cluster_offers_spot_capacity" {
  command = apply

  assert {
    condition     = toset(aws_ecs_cluster_capacity_providers.this.capacity_providers) == toset(["FARGATE", "FARGATE_SPOT"])
    error_message = "Long-running services (Airflow) can use Fargate Spot in this cluster."
  }
}

run "no_sidecars_by_default" {
  command = apply

  assert {
    condition     = length(jsondecode(aws_ecs_task_definition.this.container_definitions)) == 1
    error_message = "Only the elt container unless sidecars are configured."
  }
}

run "a_mock_api_sidecar_starts_before_elt_and_never_fails_the_task" {
  command = apply

  variables {
    sidecars = [{
      name         = "mock-netsuite"
      image        = "123456789012.dkr.ecr.us-east-1.amazonaws.com/elt-suite-mocks:0123abc"
      command      = ["netsuite", "--host", "127.0.0.1"]
      health_check = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8005/docs')"]
    }]
  }

  assert {
    condition = (
      [for c in jsondecode(aws_ecs_task_definition.this.container_definitions) : c.essential if c.name == "mock-netsuite"][0] == false
    )
    error_message = "A sidecar must be non-essential so the task ends when elt does."
  }
  assert {
    condition = contains(
      [for c in jsondecode(aws_ecs_task_definition.this.container_definitions) : c.dependsOn if c.name == "elt"][0],
      { containerName = "mock-netsuite", condition = "HEALTHY" }
    )
    error_message = "elt waits for the sidecar's health check before it starts."
  }
  assert {
    condition = (
      [for c in jsondecode(aws_ecs_task_definition.this.container_definitions) : c.readonlyRootFilesystem if c.name == "mock-netsuite"][0] &&
      [for c in jsondecode(aws_ecs_task_definition.this.container_definitions) : c.user if c.name == "mock-netsuite"][0] == "10001"
    )
    error_message = "Sidecars are hardened like the elt container."
  }
}
