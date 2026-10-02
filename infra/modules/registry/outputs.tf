output "repository_url" {
  description = "Push/pull URL, e.g. 123456789012.dkr.ecr.us-east-1.amazonaws.com/elt-suite."
  value       = aws_ecr_repository.this.repository_url
}

output "repository_name" {
  value = aws_ecr_repository.this.name
}
