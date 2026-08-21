terraform {
  required_version = ">= 1.14"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # First-time setup (new AWS account):
  #   1. cp terraform.tfvars.example terraform.tfvars && cp backend.hcl.example backend.hcl
  #   2. Edit both files with your account ID and bucket name
  #   3. Comment out the backend block below
  #   4. terraform init && terraform apply  (creates the S3 bucket with local state)
  #   5. Uncomment the backend block
  #   6. terraform init -backend-config=backend.hcl -migrate-state
  #   7. rm terraform.tfstate terraform.tfstate.backup
  backend "s3" {}
}
