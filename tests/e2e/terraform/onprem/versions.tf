terraform {
  required_version = ">= 1.6"

  # Local state per environment: meteorcloud-testenv passes
  # -backend-config=path=tests/e2e/.state/<env>/terraform.tfstate.
  backend "local" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
