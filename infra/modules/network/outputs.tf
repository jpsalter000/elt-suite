output "vpc_id" {
  value = aws_vpc.this.id
}

output "public_subnet_ids" {
  description = "Where Fargate tasks run (with a public IP and the task security group)."
  value       = aws_subnet.public[*].id
}

output "isolated_subnet_ids" {
  description = "Where the database runs; no route out of the VPC."
  value       = aws_subnet.isolated[*].id
}

output "task_security_group_id" {
  value = aws_security_group.task.id
}

output "database_security_group_id" {
  value = aws_security_group.database.id
}
