# Terraform Infrastructure

Each stack has two config files to set up locally:

- `terraform.tfvars` — input variables (from `terraform.tfvars.example`)
- `backend.hcl` — remote state config (from `backend.hcl.example`)

## Prerequisites

- Terraform >= 1.14
- AWS CLI configured with credentials for the target account
- Set `AWS_PROFILE` before running any Terraform commands:

```bash
export AWS_PROFILE=<your-profile>

# Find your 12-digit account ID (needed for terraform.tfvars and backend.hcl)
aws sts get-caller-identity --query Account --output text
```

## Stacks

### bootstrap

Creates the S3 bucket for Terraform state. All other stacks store their state here.

```bash
cd bootstrap

# Set up config files
cp terraform.tfvars.example terraform.tfvars
cp backend.hcl.example backend.hcl
# Edit both — set your account ID and bucket name

# Step 1: create the bucket (local state)
terraform init
terraform apply

# Step 2: uncomment `backend "s3" {}` in terraform.tf, then migrate state to S3
terraform init -backend-config=backend.hcl -migrate-state
rm terraform.tfstate terraform.tfstate.backup
```
