# Registry: an ECR repository for the elt-suite image.

mock_provider "aws" {}

variables {
  name = "elt-test"
}

run "images_are_immutable_scanned_and_encrypted" {
  command = apply

  assert {
    condition     = aws_ecr_repository.this.name == "elt-test"
    error_message = "Repository should be named after var.name."
  }
  assert {
    condition     = aws_ecr_repository.this.image_tag_mutability == "IMMUTABLE"
    error_message = "Tags are git SHAs and must never be overwritten."
  }
  assert {
    condition     = aws_ecr_repository.this.image_scanning_configuration[0].scan_on_push
    error_message = "Images must be scanned on push."
  }
  assert {
    condition     = aws_ecr_repository.this.encryption_configuration[0].encryption_type == "AES256"
    error_message = "Images must be encrypted at rest."
  }
  assert {
    condition     = !aws_ecr_repository.this.force_delete
    error_message = "Destroying the repository must not silently delete images by default."
  }
}

run "old_images_expire" {
  command = apply

  assert {
    condition     = jsondecode(aws_ecr_lifecycle_policy.this.policy).rules[0].selection.countNumber == 10
    error_message = "Keep only the 10 most recent images to stay within the free 500 MB."
  }
  assert {
    condition     = aws_ecr_lifecycle_policy.this.repository == aws_ecr_repository.this.name
    error_message = "Lifecycle policy must apply to the repository."
  }
}
