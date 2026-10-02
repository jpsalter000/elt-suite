# ECS cluster and Fargate task definition that run the elt CLI as a batch job.
#
# The task runs in the public subnets with a public IP (there is no NAT Gateway)
# and the no-inbound task security group. The container is hardened: non-root
# user, read-only root filesystem, and only an ephemeral /tmp is writable.
#
# Credentials never appear in the task definition: the database login comes from
# the RDS-managed Secrets Manager secret, and consumer credentials from SSM
# Parameter Store. They are injected by the execution role, which can read only
# those specific secrets.

data "aws_region" "current" {}

locals {
  container = "elt"

  environment = merge(
    {
      # No credentials in the DSN: libpq fills host, user and password from PG* variables.
      WAREHOUSE_DSN = "postgresql:///${var.database.name}?sslmode=require"
      PGHOST        = var.database.host
      PGPORT        = tostring(var.database.port)
    },
    var.environment,
  )

  secrets = merge(
    {
      PGUSER     = "${var.database.secret_arn}:username::"
      PGPASSWORD = "${var.database.secret_arn}:password::"
    },
    var.consumer_secrets,
  )
}

resource "aws_ecs_cluster" "this" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}

# --- IAM ---------------------------------------------------------------------------------

locals {
  ecs_tasks_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Used by ECS itself to pull the image, write logs and resolve secrets.
resource "aws_iam_role" "execution" {
  name               = "${var.name}-task-execution"
  assume_role_policy = local.ecs_tasks_trust
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "read-task-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [{
        Sid      = "DatabaseSecret"
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = var.database.secret_arn
      }],
      length(var.consumer_secrets) == 0 ? [] : [{
        Sid      = "ConsumerCredentials"
        Effect   = "Allow"
        Action   = "ssm:GetParameters"
        Resource = sort(values(var.consumer_secrets))
      }],
    )
  })
}

# Assumed by the running container. The pipeline needs no AWS API access today,
# so this role has no policies; it exists so permissions can be added explicitly.
resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = local.ecs_tasks_trust
}

# --- task definition ---------------------------------------------------------------------

resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([{
    name                   = local.container
    image                  = "${var.repository_url}:${var.image_tag}"
    essential              = true
    command                = var.command
    user                   = "10001"
    readonlyRootFilesystem = true
    mountPoints            = [{ sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }]
    environment            = [for k in sort(keys(local.environment)) : { name = k, value = local.environment[k] }]
    secrets                = [for k in sort(keys(local.secrets)) : { name = k, valueFrom = local.secrets[k] }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this.name
        "awslogs-region"        = data.aws_region.current.region
        "awslogs-stream-prefix" = local.container
      }
    }
  }])
}

# --- optional schedule -------------------------------------------------------------------

resource "aws_iam_role" "scheduler" {
  count = var.schedule_expression == null ? 0 : 1

  name = "${var.name}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  count = var.schedule_expression == null ? 0 : 1

  name = "run-task"
  role = aws_iam_role.scheduler[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "ecs:RunTask"
        Resource  = "${aws_ecs_task_definition.this.arn_without_revision}:*"
        Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn } }
      },
      {
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn]
      },
    ]
  })
}

resource "aws_scheduler_schedule" "this" {
  count = var.schedule_expression == null ? 0 : 1

  name                         = var.name
  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.this.arn
    role_arn = aws_iam_role.scheduler[0].arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.this.arn_without_revision
      launch_type         = "FARGATE"
      task_count          = 1

      network_configuration {
        subnets          = var.subnet_ids
        security_groups  = [var.security_group_id]
        assign_public_ip = true # no NAT Gateway; the SG still allows no inbound
      }
    }

    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}
