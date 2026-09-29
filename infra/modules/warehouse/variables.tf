variable "name" {
  description = "Identifier prefix for the instance and its groups."
  type        = string
}

variable "subnet_ids" {
  description = "Isolated subnets (at least two AZs) for the DB subnet group."
  type        = list(string)
}

variable "security_group_id" {
  description = "Security group allowing Postgres from the tasks only."
  type        = string
}

variable "instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "engine_version" {
  type    = string
  default = "16"
}

variable "deletion_protection" {
  description = "Block deletion and keep a final snapshot. Off by default for a disposable demo."
  type        = bool
  default     = false
}
