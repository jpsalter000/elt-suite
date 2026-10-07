# dev: the full elt-suite stack in one AWS account and region.
#
# State lives in the S3 bucket created in the AWS setup guide. The bucket name is
# passed at init time so it isn't hard-coded:
#
#   terraform init -backend-config="bucket=elt-suite-tfstate-<ACCOUNT_ID>"

terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }

  backend "s3" {
    key          = "elt-suite/dev/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "elt-suite"
      Environment = "dev"
      ManagedBy   = "terraform"
      Repository  = "github.com/jpsalter000/elt-suite"
    }
  }
}

locals {
  name = "elt-suite-dev"

  # Dev has no NetSuite account: the runner gets the NetSuite mock as a sidecar and
  # the Vandelay consumer points at it. These are the mock's public demo credentials.
  mock_netsuite_url = "http://127.0.0.1:8005"
  mock_netsuite_environment = {
    VANDELAY_NS_BASE_URL        = local.mock_netsuite_url
    VANDELAY_NS_ACCOUNT_ID      = "VANDELAY_SB1"
    VANDELAY_NS_CONSUMER_KEY    = "vandelay-demo-consumer-key"
    VANDELAY_NS_CONSUMER_SECRET = "vandelay-demo-consumer-secret"
    VANDELAY_NS_TOKEN_ID        = "vandelay-demo-token-id"
    VANDELAY_NS_TOKEN_SECRET    = "vandelay-demo-token-secret"
  }
}

module "network" {
  source = "../../modules/network"

  name = local.name
}

module "registry" {
  source = "../../modules/registry"

  name = "elt-suite"
}

module "registry_airflow" {
  source = "../../modules/registry"

  name = "elt-suite-airflow"
}

module "registry_mocks" {
  source = "../../modules/registry"

  name = "elt-suite-mocks"
}

module "warehouse" {
  source = "../../modules/warehouse"

  name              = local.name
  subnet_ids        = module.network.isolated_subnet_ids
  security_group_id = module.network.database_security_group_id
}

module "runner" {
  source = "../../modules/runner"

  name              = local.name
  repository_url    = module.registry.repository_url
  image_tag         = var.image_tag
  subnet_ids        = module.network.public_subnet_ids
  security_group_id = module.network.task_security_group_id

  database = {
    host       = module.warehouse.host
    port       = module.warehouse.port
    name       = module.warehouse.database_name
    secret_arn = module.warehouse.master_user_secret_arn
  }

  command          = ["pipeline", "run", "utilization_daily"]
  consumer_secrets = var.consumer_secrets
  environment      = var.mock_netsuite ? local.mock_netsuite_environment : {}
  sidecars = var.mock_netsuite ? [{
    name         = "mock-netsuite"
    image        = "${module.registry_mocks.repository_url}:${var.image_tag}"
    command      = ["netsuite", "--host", "127.0.0.1"]
    health_check = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('${local.mock_netsuite_url}/docs')"]
  }] : []

  # Airflow is the orchestrator; the EventBridge schedule is only the fallback, so
  # a pipeline never runs from both.
  schedule_expression = var.enable_airflow ? null : var.schedule_expression
}

module "airflow" {
  source = "../../modules/airflow"
  count  = var.enable_airflow ? 1 : 0

  name              = "${local.name}-airflow"
  repository_url    = module.registry_airflow.repository_url
  image_tag         = var.image_tag
  subnet_ids        = module.network.public_subnet_ids
  security_group_id = module.network.task_security_group_id
  desired_count     = var.airflow_desired_count
  capacity_provider = var.airflow_capacity_provider

  database = {
    host              = module.warehouse.host
    port              = module.warehouse.port
    master_secret_arn = module.warehouse.master_user_secret_arn
  }

  elt = {
    cluster_arn                     = module.runner.cluster_arn
    cluster_name                    = module.runner.cluster_name
    task_definition_family          = module.runner.task_definition_family
    task_definition_arn_no_revision = module.runner.task_definition_arn_without_revision
    container                       = module.runner.container_name
    execution_role_arn              = module.runner.execution_role_arn
    task_role_arn                   = module.runner.task_role_arn
    log_group_name                  = module.runner.log_group_name
    log_group_arn                   = module.runner.log_group_arn
  }
}
