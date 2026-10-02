# dev: the full elt-suite stack in one AWS account and region.
#
# State lives in the S3 bucket created in the AWS setup guide. The bucket name is
# passed at init time so it isn't hard-coded:
#
#   terraform init -backend-config="bucket=elt-suite-tfstate-<ACCOUNT_ID>"

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
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
}

module "network" {
  source = "../../modules/network"

  name = local.name
}

module "registry" {
  source = "../../modules/registry"

  name = "elt-suite"
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

  consumer_secrets    = var.consumer_secrets
  schedule_expression = var.schedule_expression
}
