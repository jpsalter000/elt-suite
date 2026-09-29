# Private RDS Postgres used as the pipeline's destination.
#
# - Lives in isolated subnets and accepts only the security group it is given.
# - TLS is enforced server-side (rds.force_ssl).
# - RDS generates the master password and keeps it in Secrets Manager
#   (manage_master_user_password), so it never appears in Terraform state or code.
# - Sized and configured for a demo; set deletion_protection = true for real data.

resource "aws_db_subnet_group" "this" {
  name       = var.name
  subnet_ids = var.subnet_ids
}

resource "aws_db_parameter_group" "this" {
  name   = "${var.name}-pg16"
  family = "postgres16"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "this" {
  identifier     = var.name
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  db_name                     = "warehouse"
  username                    = "elt"
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [var.security_group_id]
  parameter_group_name   = aws_db_parameter_group.this.name
  publicly_accessible    = false
  multi_az               = false

  backup_retention_period      = 1
  performance_insights_enabled = false
  auto_minor_version_upgrade   = true
  apply_immediately            = true

  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = !var.deletion_protection
  final_snapshot_identifier = var.deletion_protection ? "${var.name}-final" : null
}
