# Network: public subnets for Fargate tasks (public IP, no inbound), isolated subnets
# for RDS (no route out), security groups + NACLs as two firewall layers, flow logs.

mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = {
      names = ["us-east-1a", "us-east-1b", "us-east-1c"]
    }
  }
}

variables {
  name = "elt-test"
}

run "two_tiers_across_two_azs" {
  command = apply

  assert {
    condition     = length(aws_subnet.public) == 2 && length(aws_subnet.isolated) == 2
    error_message = "Expected two public and two isolated subnets."
  }
  assert {
    condition     = aws_subnet.public[0].availability_zone != aws_subnet.public[1].availability_zone
    error_message = "Public subnets must be in different AZs."
  }
  assert {
    condition     = aws_subnet.isolated[0].availability_zone != aws_subnet.isolated[1].availability_zone
    error_message = "Isolated subnets must be in different AZs (RDS subnet groups require two)."
  }
  assert {
    condition     = alltrue([for s in concat(aws_subnet.public, aws_subnet.isolated) : !s.map_public_ip_on_launch])
    error_message = "No subnet should hand out public IPs by default; tasks opt in explicitly."
  }
}

run "only_public_subnets_route_to_the_internet" {
  command = apply

  assert {
    condition     = one(aws_route_table.public.route).cidr_block == "0.0.0.0/0"
    error_message = "Public route table needs exactly one default route."
  }
  assert {
    condition     = one(aws_route_table.public.route).gateway_id == aws_internet_gateway.this.id
    error_message = "Public default route must go to the internet gateway."
  }
  assert {
    condition     = length(aws_route_table.isolated.route) == 0
    error_message = "Isolated route table must have no routes beyond the implicit local route."
  }
  assert {
    condition     = alltrue([for a in aws_route_table_association.isolated : a.route_table_id == aws_route_table.isolated.id])
    error_message = "Isolated subnets must use the isolated route table."
  }
}

run "tasks_accept_no_inbound_and_egress_is_narrow" {
  command = apply

  assert {
    condition     = length(aws_security_group.task.ingress) == 0
    error_message = "The task security group must not allow any inbound traffic."
  }
  assert {
    condition = (
      aws_vpc_security_group_egress_rule.task_https.from_port == 443 &&
      aws_vpc_security_group_egress_rule.task_https.to_port == 443 &&
      aws_vpc_security_group_egress_rule.task_https.ip_protocol == "tcp" &&
      aws_vpc_security_group_egress_rule.task_https.cidr_ipv4 == "0.0.0.0/0"
    )
    error_message = "Tasks may reach the internet on HTTPS only."
  }
  assert {
    condition = (
      aws_vpc_security_group_egress_rule.task_postgres.from_port == 5432 &&
      aws_vpc_security_group_egress_rule.task_postgres.referenced_security_group_id == aws_security_group.database.id
    )
    error_message = "Tasks may reach Postgres only on the database security group."
  }
}

run "database_is_reachable_only_from_tasks" {
  command = apply

  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.database_from_task.from_port == 5432 &&
      aws_vpc_security_group_ingress_rule.database_from_task.to_port == 5432 &&
      aws_vpc_security_group_ingress_rule.database_from_task.referenced_security_group_id == aws_security_group.task.id &&
      aws_vpc_security_group_ingress_rule.database_from_task.cidr_ipv4 == null
    )
    error_message = "Postgres ingress must come only from the task security group."
  }
  assert {
    condition     = length(aws_security_group.database.egress) == 0
    error_message = "The database needs no outbound rules."
  }
}

run "default_security_group_denies_everything" {
  command = apply

  assert {
    condition     = length(aws_default_security_group.this.ingress) == 0 && length(aws_default_security_group.this.egress) == 0
    error_message = "The VPC default security group must have no rules."
  }
}

run "nacls_mirror_the_security_groups" {
  command = apply

  assert {
    condition     = alltrue([for r in aws_network_acl.isolated.ingress : r.from_port == 5432 && r.to_port == 5432 && contains(aws_subnet.public[*].cidr_block, r.cidr_block)])
    error_message = "Isolated subnets accept only Postgres from the public subnets."
  }
  assert {
    condition     = alltrue([for r in aws_network_acl.isolated.egress : r.from_port == 1024 && r.to_port == 65535 && contains(aws_subnet.public[*].cidr_block, r.cidr_block)])
    error_message = "Isolated subnets may only answer the public subnets on ephemeral ports."
  }
  assert {
    condition     = alltrue([for r in aws_network_acl.public.ingress : r.from_port >= 1024])
    error_message = "Public subnets accept only return traffic (ephemeral ports), never new inbound connections."
  }
  assert {
    condition     = toset([for r in aws_network_acl.public.egress : r.to_port]) == toset([443, 5432])
    error_message = "Public subnets may send only HTTPS and Postgres traffic."
  }
}

run "rejected_traffic_is_logged" {
  command = apply

  assert {
    condition     = aws_flow_log.this.traffic_type == "REJECT" && aws_flow_log.this.vpc_id == aws_vpc.this.id
    error_message = "VPC flow logs should capture rejected traffic."
  }
  assert {
    condition     = aws_cloudwatch_log_group.flow_logs.retention_in_days == 14
    error_message = "Flow logs must not be kept forever."
  }
}

run "rejects_a_single_az" {
  command = plan

  variables {
    az_count = 1
  }
  expect_failures = [var.az_count]
}
