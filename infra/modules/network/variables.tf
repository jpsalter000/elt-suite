variable "name" {
  description = "Name prefix for every resource."
  type        = string
}

variable "cidr_block" {
  description = "VPC CIDR. Public subnets take x.x.0-9.0/24, isolated subnets x.x.100-109.0/24."
  type        = string
  default     = "10.20.0.0/16"
}

variable "az_count" {
  description = "Availability zones to span. RDS subnet groups need at least two."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3: RDS needs subnets in at least two AZs."
  }
}

variable "log_retention_days" {
  description = "Days to keep VPC flow logs."
  type        = number
  default     = 14
}
