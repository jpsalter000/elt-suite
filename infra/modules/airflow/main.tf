# Self-hosted Airflow on Fargate that orchestrates elt-suite pipelines.
#
# One service runs one task with the four Airflow 3 components and LocalExecutor,
# so they share localhost. Pipeline work does not run here: every DAG task starts
# the runner's task definition (EcsRunTaskOperator) with `elt pipeline run-task`.
#
# Nothing is exposed. The service has no load balancer and the task security group
# allows no inbound traffic; operators reach the UI with ECS Exec port forwarding
# (aws ssm start-session to the api-server container). Generated secrets are written to Secrets Manager
# write-only, so they never appear in Terraform state.

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  region    = data.aws_region.current.region
  image     = "${var.repository_url}:${var.image_tag}"
  api_port  = 8080
  secret    = aws_secretsmanager_secret.this.arn
  task_arns = "arn:aws:ecs:${local.region}:${data.aws_caller_identity.current.account_id}:task/${var.elt.cluster_name}/*"

  environment = {
    AIRFLOW__CORE__EXECUTOR                 = "LocalExecutor"
    AIRFLOW__CORE__EXECUTION_API_SERVER_URL = "http://localhost:${local.api_port}/execution/"
    AIRFLOW__CORE__AUTH_MANAGER             = "airflow.providers.fab.auth_manager.fab_auth_manager.FabAuthManager"
    AIRFLOW__CORE__LOAD_EXAMPLES            = "False"
    AIRFLOW__API__WORKERS                   = "1"
    # db.t4g.micro allows ~80 connections and also serves the warehouse.
    AIRFLOW__DATABASE__SQL_ALCHEMY_POOL_SIZE    = "2"
    AIRFLOW__DATABASE__SQL_ALCHEMY_MAX_OVERFLOW = "3"
    AIRFLOW__LOGGING__REMOTE_LOGGING            = "True"
    AIRFLOW__LOGGING__REMOTE_BASE_LOG_FOLDER    = "cloudwatch://${aws_cloudwatch_log_group.task_logs.arn}"
    AIRFLOW__LOGGING__REMOTE_LOG_CONN_ID        = "aws_default"
    AIRFLOW_DB_HOST                             = var.database.host
    AIRFLOW_DB_PORT                             = tostring(var.database.port)
    AIRFLOW_DB_NAME                             = "airflow"
    AIRFLOW_DB_USER                             = "airflow"
    AIRFLOW_DB_SSLMODE                          = "require"
    AWS_DEFAULT_REGION                          = local.region
    ELT_RUNNER                                  = "ecs"
    ELT_ECS_CLUSTER                             = var.elt.cluster_name
    ELT_ECS_TASK_DEFINITION                     = var.elt.task_definition_family
    ELT_ECS_CONTAINER                           = var.elt.container
    ELT_ECS_SUBNETS                             = join(",", var.subnet_ids)
    ELT_ECS_SECURITY_GROUPS                     = var.security_group_id
    ELT_ECS_LOG_GROUP                           = var.elt.log_group_name
  }

  secrets = {
    AIRFLOW__CORE__FERNET_KEY     = "${local.secret}:fernet_key::"
    AIRFLOW__API_AUTH__JWT_SECRET = "${local.secret}:jwt_secret::"
    AIRFLOW__API__SECRET_KEY      = "${local.secret}:api_secret_key::"
    AIRFLOW_DB_PASSWORD           = "${local.secret}:db_password::"
    _AIRFLOW_WWW_USER_PASSWORD    = "${local.secret}:admin_password::"
  }

  # Only init logs in as the RDS master user, to create Airflow's role and database.
  init_secrets = merge(local.secrets, {
    PGUSER     = "${var.database.master_secret_arn}:username::"
    PGPASSWORD = "${var.database.master_secret_arn}:password::"
  })

  components = {
    "api-server"    = ["api-server", "--port", tostring(local.api_port)]
    "scheduler"     = ["scheduler"]
    "dag-processor" = ["dag-processor"]
    "triggerer"     = ["triggerer"]
  }

  log_options = {
    "awslogs-group"         = aws_cloudwatch_log_group.this.name
    "awslogs-region"        = local.region
    "awslogs-stream-prefix" = "airflow"
  }

  init_container = {
    name             = "init"
    image            = local.image
    essential        = false
    command          = ["bootstrap"]
    environment      = [for k in sort(keys(local.environment)) : { name = k, value = local.environment[k] }]
    secrets          = [for k in sort(keys(local.init_secrets)) : { name = k, valueFrom = local.init_secrets[k] }]
    logConfiguration = { logDriver = "awslogs", options = local.log_options }
  }

  api_server_extras = {
    portMappings = [{ containerPort = local.api_port, protocol = "tcp" }]
    healthCheck = {
      command     = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:${local.api_port}/api/v2/monitor/health')"]
      interval    = 30
      timeout     = 10
      retries     = 5
      startPeriod = 120
    }
  }

  component_containers = [
    for name in ["api-server", "scheduler", "dag-processor", "triggerer"] : merge(
      {
        name             = name
        image            = local.image
        essential        = true
        command          = local.components[name]
        dependsOn        = [{ containerName = "init", condition = "SUCCESS" }]
        environment      = [for k in sort(keys(local.environment)) : { name = k, value = local.environment[k] }]
        secrets          = [for k in sort(keys(local.secrets)) : { name = k, valueFrom = local.secrets[k] }]
        logConfiguration = { logDriver = "awslogs", options = local.log_options }
      },
      # Only the api-server publishes a port and has a health check.
    [for extra in [local.api_server_extras] : extra if name == "api-server"]...)
  ]
}

# --- secrets -----------------------------------------------------------------------------

ephemeral "random_password" "db" {
  length  = 32
  special = false
}

# A Fernet key is 32 random bytes in URL-safe base64: 43 characters from that
# alphabet plus "=" decodes to exactly 32 bytes.
ephemeral "random_password" "fernet" {
  length           = 43
  override_special = "-_"
}

ephemeral "random_password" "jwt" {
  length  = 48
  special = false
}

ephemeral "random_password" "api" {
  length  = 48
  special = false
}

ephemeral "random_password" "admin" {
  length  = 24
  special = false
}

resource "aws_secretsmanager_secret" "this" {
  name                    = "${var.name}/airflow"
  description             = "Airflow keys and passwords (generated by Terraform, never stored in state)."
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "this" {
  secret_id = aws_secretsmanager_secret.this.id
  secret_string_wo = jsonencode({
    db_password    = ephemeral.random_password.db.result
    fernet_key     = "${ephemeral.random_password.fernet.result}="
    jwt_secret     = ephemeral.random_password.jwt.result
    api_secret_key = ephemeral.random_password.api.result
    admin_password = ephemeral.random_password.admin.result
  })
  # Bump to rotate every generated secret on the next apply.
  secret_string_wo_version = var.secrets_version
}

# --- logs ----------------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}

# Airflow's own task logs (remote logging); the elt task logs stay in the runner's group.
resource "aws_cloudwatch_log_group" "task_logs" {
  name              = "/ecs/${var.name}/task-logs"
  retention_in_days = var.log_retention_days
}

# --- IAM -----------------------------------------------------------------------------------

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

resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = local.ecs_tasks_trust
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "read-airflow-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "secretsmanager:GetSecretValue"
      Resource = [aws_secretsmanager_secret.this.arn, var.database.master_secret_arn]
    }]
  })
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = local.ecs_tasks_trust
}

resource "aws_iam_role_policy" "task" {
  name = "orchestrate-elt"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "StartEltTasks"
        Effect    = "Allow"
        Action    = "ecs:RunTask"
        Resource  = "${var.elt.task_definition_arn_no_revision}:*"
        Condition = { ArnEquals = { "ecs:cluster" = var.elt.cluster_arn } }
      },
      {
        Sid      = "WatchEltTasks"
        Effect   = "Allow"
        Action   = ["ecs:DescribeTasks", "ecs:StopTask"]
        Resource = local.task_arns
      },
      {
        Sid      = "PassEltRoles"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = [var.elt.execution_role_arn, var.elt.task_role_arn]
      },
      {
        Sid      = "ReadEltTaskLogs"
        Effect   = "Allow"
        Action   = "logs:GetLogEvents"
        Resource = "${var.elt.log_group_arn}:log-stream:*"
      },
      {
        Sid      = "AirflowTaskLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:GetLogEvents"]
        Resource = "${aws_cloudwatch_log_group.task_logs.arn}:log-stream:*"
      },
      {
        Sid    = "EcsExec"
        Effect = "Allow"
        Action = [
          "ssmmessages:CreateControlChannel",
          "ssmmessages:CreateDataChannel",
          "ssmmessages:OpenControlChannel",
          "ssmmessages:OpenDataChannel",
        ]
        Resource = "*"
      },
    ]
  })
}

# --- task definition and service ---------------------------------------------------------

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

  container_definitions = jsonencode(concat([local.init_container], local.component_containers))
}

resource "aws_ecs_service" "this" {
  name                   = var.name
  cluster                = var.elt.cluster_arn
  task_definition        = aws_ecs_task_definition.this.arn
  desired_count          = var.desired_count
  enable_execute_command = true

  # LocalExecutor: never run two schedulers, even during a deployment.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  capacity_provider_strategy {
    capacity_provider = var.capacity_provider
    weight            = 1
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.security_group_id]
    assign_public_ip = true # no NAT Gateway; the SG allows no inbound
  }
}
