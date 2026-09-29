output "host" {
  value = aws_db_instance.this.address
}

output "port" {
  value = aws_db_instance.this.port
}

output "database_name" {
  value = aws_db_instance.this.db_name
}

output "master_user_secret_arn" {
  description = "Secrets Manager secret (JSON with username and password) managed by RDS."
  value       = one(aws_db_instance.this.master_user_secret[*].secret_arn)
}
