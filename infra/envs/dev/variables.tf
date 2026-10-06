variable "region" {
  type    = string
  default = "us-east-1"
}

variable "image_tag" {
  description = "Git SHA of the image to run. CI sets this; tags are immutable."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{7,40}$", var.image_tag))
    error_message = "image_tag must be a git commit SHA (7-40 hex characters), not a mutable tag like 'latest'."
  }
}

variable "consumer_secrets" {
  description = "Environment variable name => SSM parameter ARN for consumer credentials."
  type        = map(string)
  default     = {}
}

variable "schedule_expression" {
  description = "Optional EventBridge Scheduler expression for a recurring run, e.g. \"cron(0 6 * * ? *)\"."
  type        = string
  default     = null
}

variable "enable_airflow" {
  description = "Run self-hosted Airflow (ADR 0003). When false, schedule_expression drives the runner instead."
  type        = bool
  default     = true
}

variable "airflow_desired_count" {
  description = "1 runs Airflow; 0 pauses it without destroying anything."
  type        = number
  default     = 1
}

variable "airflow_capacity_provider" {
  description = "FARGATE_SPOT (default, cheaper) or FARGATE."
  type        = string
  default     = "FARGATE_SPOT"
}

variable "mock_netsuite" {
  description = "Run the NetSuite mock as a sidecar of the elt task (dev has no NetSuite account)."
  type        = bool
  default     = true
}
