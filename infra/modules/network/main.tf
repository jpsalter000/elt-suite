# Two-tier VPC with no NAT Gateway (saves ~$33/month):
#
#   public subnets   -> Fargate tasks. Default route to the internet gateway; tasks
#                       get a public IP but their security group allows no inbound.
#   isolated subnets -> RDS. No route out of the VPC at all.
#
# Security groups (stateful) and network ACLs (stateless) enforce the same rules,
# so one misconfigured layer does not open anything. Rejected traffic is logged.

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs          = slice(data.aws_availability_zones.available.names, 0, var.az_count)
  public_cidrs = [for i in range(var.az_count) : cidrsubnet(var.cidr_block, 8, i)]
  isolated     = [for i in range(var.az_count) : cidrsubnet(var.cidr_block, 8, 100 + i)]
  anywhere     = "0.0.0.0/0"
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = var.name }
}

# The default security group is attached to anything created without one; strip it.
resource "aws_default_security_group" "this" {
  vpc_id  = aws_vpc.this.id
  ingress = []
  egress  = []

  tags = { Name = "${var.name}-default-deny" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = { Name = var.name }
}

# --- subnets & routing -----------------------------------------------------------------

resource "aws_subnet" "public" {
  count = var.az_count

  vpc_id                  = aws_vpc.this.id
  cidr_block              = local.public_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = false # tasks opt in with assign_public_ip

  tags = { Name = "${var.name}-public-${local.azs[count.index]}", Tier = "public" }
}

resource "aws_subnet" "isolated" {
  count = var.az_count

  vpc_id                  = aws_vpc.this.id
  cidr_block              = local.isolated[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = false

  tags = { Name = "${var.name}-isolated-${local.azs[count.index]}", Tier = "isolated" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = local.anywhere
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "${var.name}-public" }
}

resource "aws_route_table" "isolated" {
  vpc_id = aws_vpc.this.id
  route  = [] # explicit: also removes any route added out of band

  tags = { Name = "${var.name}-isolated" }
}

resource "aws_route_table_association" "public" {
  count = var.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "isolated" {
  count = var.az_count

  subnet_id      = aws_subnet.isolated[count.index].id
  route_table_id = aws_route_table.isolated.id
}

# --- security groups (stateful firewall) -----------------------------------------------

resource "aws_security_group" "task" {
  name        = "${var.name}-task"
  description = "Fargate tasks: no inbound; HTTPS out and Postgres to the database"
  vpc_id      = aws_vpc.this.id
  ingress     = []

  tags = { Name = "${var.name}-task" }
}

resource "aws_vpc_security_group_egress_rule" "task_https" {
  security_group_id = aws_security_group.task.id
  description       = "Source APIs, ECR, CloudWatch Logs, Secrets Manager, SSM"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = local.anywhere
}

resource "aws_vpc_security_group_egress_rule" "task_postgres" {
  security_group_id            = aws_security_group.task.id
  description                  = "Warehouse"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.database.id
}

resource "aws_security_group" "database" {
  name        = "${var.name}-database"
  description = "Warehouse: Postgres from Fargate tasks only; no outbound"
  vpc_id      = aws_vpc.this.id
  egress      = []

  tags = { Name = "${var.name}-database" }
}

resource "aws_vpc_security_group_ingress_rule" "database_from_task" {
  security_group_id            = aws_security_group.database.id
  description                  = "Postgres from Fargate tasks"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.task.id
}

# --- network ACLs (stateless firewall) -------------------------------------------------

resource "aws_network_acl" "public" {
  vpc_id     = aws_vpc.this.id
  subnet_ids = aws_subnet.public[*].id

  # Return traffic only: responses from the internet and from the database.
  ingress {
    rule_no    = 100
    action     = "allow"
    protocol   = "tcp"
    cidr_block = local.anywhere
    from_port  = 1024
    to_port    = 65535
  }

  egress {
    rule_no    = 100
    action     = "allow"
    protocol   = "tcp"
    cidr_block = local.anywhere
    from_port  = 443
    to_port    = 443
  }

  dynamic "egress" {
    for_each = local.isolated
    content {
      rule_no    = 200 + egress.key
      action     = "allow"
      protocol   = "tcp"
      cidr_block = egress.value
      from_port  = 5432
      to_port    = 5432
    }
  }

  tags = { Name = "${var.name}-public" }
}

resource "aws_network_acl" "isolated" {
  vpc_id     = aws_vpc.this.id
  subnet_ids = aws_subnet.isolated[*].id

  dynamic "ingress" {
    for_each = local.public_cidrs
    content {
      rule_no    = 100 + ingress.key
      action     = "allow"
      protocol   = "tcp"
      cidr_block = ingress.value
      from_port  = 5432
      to_port    = 5432
    }
  }

  dynamic "egress" {
    for_each = local.public_cidrs
    content {
      rule_no    = 100 + egress.key
      action     = "allow"
      protocol   = "tcp"
      cidr_block = egress.value
      from_port  = 1024
      to_port    = 65535
    }
  }

  tags = { Name = "${var.name}-isolated" }
}

# --- flow logs -------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "flow_logs" {
  name              = "/vpc/${var.name}/flow-logs"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "flow_logs" {
  name = "${var.name}-vpc-flow-logs"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "vpc-flow-logs.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "flow_logs" {
  name = "write-flow-logs"
  role = aws_iam_role.flow_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
      Resource = "${aws_cloudwatch_log_group.flow_logs.arn}:*"
    }]
  })
}

resource "aws_flow_log" "this" {
  vpc_id               = aws_vpc.this.id
  traffic_type         = "REJECT"
  log_destination_type = "cloud-watch-logs"
  log_destination      = aws_cloudwatch_log_group.flow_logs.arn
  iam_role_arn         = aws_iam_role.flow_logs.arn
}
