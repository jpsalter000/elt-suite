# Warehouse: a small private RDS Postgres instance for the pipeline's destination.

mock_provider "aws" {}

variables {
  name              = "elt-test"
  subnet_ids        = ["subnet-aaa", "subnet-bbb"]
  security_group_id = "sg-database"
}

run "private_encrypted_and_tls_only" {
  command = apply

  assert {
    condition     = !aws_db_instance.this.publicly_accessible
    error_message = "The database must never be publicly accessible."
  }
  assert {
    condition     = aws_db_instance.this.storage_encrypted
    error_message = "Storage must be encrypted."
  }
  assert {
    condition     = aws_db_instance.this.vpc_security_group_ids == toset(["sg-database"])
    error_message = "Only the given security group may be attached."
  }
  assert {
    condition     = aws_db_subnet_group.this.subnet_ids == toset(["subnet-aaa", "subnet-bbb"])
    error_message = "The instance must live in the given (isolated) subnets."
  }
  assert {
    condition     = one([for p in aws_db_parameter_group.this.parameter : p.value if p.name == "rds.force_ssl"]) == "1"
    error_message = "Connections must use TLS."
  }
  assert {
    condition     = aws_db_instance.this.parameter_group_name == aws_db_parameter_group.this.name
    error_message = "The instance must use the TLS-enforcing parameter group."
  }
}

run "password_is_managed_by_rds_not_terraform" {
  command = apply

  assert {
    condition     = aws_db_instance.this.manage_master_user_password
    error_message = "RDS should generate and store the master password in Secrets Manager, keeping it out of Terraform state."
  }
  assert {
    condition     = aws_db_instance.this.password == null
    error_message = "No password may be set in configuration."
  }
}

run "sized_for_a_demo" {
  command = apply

  assert {
    condition = (
      aws_db_instance.this.engine == "postgres" &&
      startswith(aws_db_instance.this.engine_version, "16") &&
      aws_db_instance.this.instance_class == "db.t4g.micro" &&
      aws_db_instance.this.allocated_storage == 20 &&
      aws_db_instance.this.storage_type == "gp3"
    )
    error_message = "Expected Postgres 16 on db.t4g.micro with 20 GB gp3."
  }
  assert {
    condition     = !aws_db_instance.this.multi_az && !aws_db_instance.this.performance_insights_enabled
    error_message = "Multi-AZ and Performance Insights double the cost; keep them off for the demo."
  }
  assert {
    condition     = aws_db_instance.this.backup_retention_period == 1 && aws_db_instance.this.db_name == "warehouse"
    error_message = "Expected 1-day backups and a database named warehouse."
  }
}

run "can_be_protected_for_real_use" {
  command = apply

  variables {
    deletion_protection = true
  }

  assert {
    condition     = aws_db_instance.this.deletion_protection && !aws_db_instance.this.skip_final_snapshot
    error_message = "Protected mode must block deletion and keep a final snapshot."
  }
}
