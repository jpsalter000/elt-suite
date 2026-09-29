output "ecr_repository_url" {
  value = module.registry.repository_url
}

output "container_image" {
  description = "Image the task definition runs."
  value       = "${module.registry.repository_url}:${var.image_tag}"
}

output "cluster_name" {
  value = module.runner.cluster_name
}

output "task_definition_family" {
  value = module.runner.task_definition_family
}

output "log_group_name" {
  value = module.runner.log_group_name
}

output "database_host" {
  value = module.warehouse.host
}

output "public_subnet_ids" {
  value = module.network.public_subnet_ids
}

output "task_security_group_id" {
  value = module.network.task_security_group_id
}

output "run_task_network" {
  description = "awsvpcConfiguration for `aws ecs run-task` (see README)."
  value = {
    subnets          = module.network.public_subnet_ids
    security_groups  = [module.network.task_security_group_id]
    assign_public_ip = "ENABLED"
  }
}
