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
  mock_resource "aws_secretsmanager_secret" {
    defaults = {
      arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:elt-suite-dev/airflow-abc"
    }
  }
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
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

# The real random provider runs here (it needs no credentials): provider mocks
# don't support the ephemeral random_password resources yet.

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

run "airflow_orchestrates_by_default_instead_of_the_schedule" {
  command = apply

  variables {
    schedule_expression = "cron(0 6 * * ? *)"
  }

  assert {
    condition     = length(module.airflow) == 1
    error_message = "Airflow is the orchestrator in dev."
  }
  assert {
    condition     = output.scheduler == "airflow"
    error_message = "With Airflow on, the EventBridge schedule must stay off or pipelines run twice."
  }
}

run "the_eventbridge_schedule_is_the_fallback_without_airflow" {
  command = apply

  variables {
    enable_airflow      = false
    schedule_expression = "cron(0 6 * * ? *)"
  }

  assert {
    condition     = length(module.airflow) == 0 && output.scheduler == "eventbridge"
    error_message = "Without Airflow, the runner's schedule runs the pipeline."
  }
}

run "every_image_has_its_own_registry" {
  command = apply

  assert {
    condition = (
      module.registry_airflow.repository_name == "elt-suite-airflow" &&
      module.registry_mocks.repository_name == "elt-suite-mocks"
    )
    error_message = "The runtime, Airflow and mock images live in separate repositories."
  }
}

run "the_demo_runs_against_a_mock_netsuite_sidecar" {
  command = apply

  assert {
    condition     = output.netsuite_source == "mock sidecar at http://127.0.0.1:8005"
    error_message = "Dev has no real NetSuite account, so the runner gets the mock as a sidecar."
  }
}
