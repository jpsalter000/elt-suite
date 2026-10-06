variable "name" {
  description = "Name for the service, task family, roles, secret and log groups."
  type        = string
}

variable "repository_url" {
  description = "ECR repository URL of the Airflow image (without tag)."
  type        = string
}

variable "image_tag" {
  description = "Airflow image tag to run; CI passes the git SHA."
  type        = string
}

variable "subnet_ids" {
  description = "Public subnets for the service and for the elt tasks it starts."
  type        = list(string)
}

variable "security_group_id" {
  description = "Task security group (no inbound) for the service and the elt tasks."
  type        = string
}

variable "database" {
  description = "The warehouse instance, which also hosts Airflow's metadata database."
  type = object({
    host              = string
    port              = number
    master_secret_arn = string
  })
}

variable "elt" {
  description = "The runner the DAGs start tasks on (outputs of the runner module)."
  type = object({
    cluster_arn                     = string
    cluster_name                    = string
    task_definition_family          = string
    task_definition_arn_no_revision = string
    container                       = string
    execution_role_arn              = string
    task_role_arn                   = string
    log_group_name                  = string
    log_group_arn                   = string
  })
}

variable "desired_count" {
  description = "1 runs Airflow; 0 pauses it (the elt runner and warehouse stay up)."
  type        = number
  default     = 1

  validation {
    condition     = contains([0, 1], var.desired_count)
    error_message = "LocalExecutor supports one scheduler: desired_count must be 0 or 1."
  }
}

variable "capacity_provider" {
  description = "FARGATE_SPOT (cheaper; Airflow recovers from interruptions) or FARGATE."
  type        = string
  default     = "FARGATE_SPOT"

  validation {
    condition     = contains(["FARGATE", "FARGATE_SPOT"], var.capacity_provider)
    error_message = "capacity_provider must be FARGATE or FARGATE_SPOT."
  }
}

variable "cpu" {
  type    = string
  default = "1024"
}

variable "memory" {
  type    = string
  default = "4096"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "secrets_version" {
  description = "Increment to regenerate and rotate Airflow's keys and passwords."
  type        = number
  default     = 1
}
