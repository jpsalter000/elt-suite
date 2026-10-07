output "service_name" {
  value = aws_ecs_service.this.name
}

output "task_definition_family" {
  value = aws_ecs_task_definition.this.family
}

output "secret_arn" {
  description = "Secrets Manager secret holding the admin password (admin_password)."
  value       = aws_secretsmanager_secret.this.arn
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.this.name
}

output "api_port" {
  description = "Port of the Airflow UI/API inside the task; reach it with ECS Exec port forwarding."
  value       = local.api_port
}
