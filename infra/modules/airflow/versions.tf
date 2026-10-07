terraform {
  required_version = ">= 1.11" # write-only attributes (secret_string_wo)

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7" # ephemeral random_password
    }
  }
}
