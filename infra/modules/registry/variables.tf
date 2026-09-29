variable "name" {
  description = "Repository name."
  type        = string
}

variable "keep_images" {
  description = "How many recent images to keep; older ones expire."
  type        = number
  default     = 10
}

variable "force_delete" {
  description = "Allow terraform destroy to delete the repository even if it still holds images."
  type        = bool
  default     = false
}
