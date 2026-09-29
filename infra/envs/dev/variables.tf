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
