# The dev environment wires the modules together. Mocked AWS; no account needed.

mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = {
      names = ["us-east-1a", "us-east-1b"]
    }
  }
  mock_data "aws_region" {
    defaults = {
      region = "us-east-1"
    }
  }
  # The provider validates ARN syntax even for mocked values.
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/elt-suite-dev"
    }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:us-east-1:123456789012:log-group:elt-suite-dev"
    }
  }
  mock_resource "aws_ecs_cluster" {
    defaults = {
      arn = "arn:aws:ecs:us-east-1:123456789012:cluster/elt-suite-dev"
    }
  }
  mock_resource "aws_ecs_task_definition" {
    defaults = {
      arn                  = "arn:aws:ecs:us-east-1:123456789012:task-definition/elt-suite-dev:1"
      arn_without_revision = "arn:aws:ecs:us-east-1:123456789012:task-definition/elt-suite-dev"
    }
  }
  mock_resource "aws_ecr_repository" {
    defaults = {
      repository_url = "123456789012.dkr.ecr.us-east-1.amazonaws.com/elt-suite"
    }
  }
  mock_resource "aws_db_instance" {
    defaults = {
      address = "elt-suite-dev.abc.us-east-1.rds.amazonaws.com"
      port    = 5432
      master_user_secret = [{
        secret_arn    = "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds!db-abc"
        kms_key_id    = "alias/aws/secretsmanager"
        secret_status = "active"
      }]
    }
  }
}

variables {
  image_tag = "0123abcd"
}

run "task_runs_the_sha_tagged_image_from_the_registry" {
  command = apply

  assert {
    condition     = output.container_image == "123456789012.dkr.ecr.us-east-1.amazonaws.com/elt-suite:0123abcd"
    error_message = "The task must run the image CI just pushed, tagged with the git SHA."
  }
}

run "task_network_is_public_subnets_with_the_no_inbound_sg" {
  command = apply

  assert {
    condition     = output.run_task_network.subnets == output.public_subnet_ids
    error_message = "Tasks run in the public subnets."
  }
  assert {
    condition     = output.run_task_network.security_groups == [output.task_security_group_id]
    error_message = "Tasks use only the task security group."
  }
  assert {
    condition     = output.run_task_network.assign_public_ip == "ENABLED"
    error_message = "Without a NAT Gateway, tasks need a public IP to reach ECR and source APIs."
  }
}

run "warehouse_is_wired_to_the_runner" {
  command = apply

  assert {
    condition     = output.database_host == "elt-suite-dev.abc.us-east-1.rds.amazonaws.com"
    error_message = "The runner must point at the warehouse instance."
  }
}

run "rejects_mutable_image_tags" {
  command = plan

  variables {
    image_tag = "latest"
  }
  expect_failures = [var.image_tag]
}
