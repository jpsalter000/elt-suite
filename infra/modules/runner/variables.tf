variable "name" {
  description = "Name for the cluster, task family, roles and log group."
  type        = string
}

variable "repository_url" {
  description = "ECR repository URL (without tag)."
  type        = string
}

variable "image_tag" {
  description = "Image tag to run; CI passes the git SHA."
  type        = string
}

variable "subnet_ids" {
  description = "Public subnets the task runs in (used by the schedule and by run-task)."
  type        = list(string)
}

variable "security_group_id" {
  description = "Task security group (no inbound)."
  type        = string
}

variable "database" {
  description = "Warehouse connection details and the RDS-managed secret holding username/password."
  type = object({
    host       = string
    port       = number
    name       = string
    secret_arn = string
  })
}

variable "consumer_secrets" {
  description = "Environment variable name => SSM parameter ARN, for consumer credentials."
  type        = map(string)
  default     = {}
}

variable "environment" {
  description = "Extra plain environment variables for the container."
  type        = map(string)
  default     = {}
}

variable "command" {
  description = "Default elt CLI arguments; override per run with ECS run-task."
  type        = list(string)
  default     = ["run", "demo_usgs_extract_and_load"]
}

variable "cpu" {
  type    = string
  default = "256"
}

variable "memory" {
  type    = string
  default = "512"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "schedule_expression" {
  description = "EventBridge Scheduler expression, e.g. \"cron(0 6 * * ? *)\". Null disables scheduling."
  type        = string
  default     = null
}
