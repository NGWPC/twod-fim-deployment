# Terraform Infrastructure

Three independent stacks, run in order: bootstrap → foundation → app.

Each stack has two config files to set up locally:

- `terraform.tfvars` — input variables (from `terraform.tfvars.example`)
- `backend.hcl` — remote state config (from `backend.hcl.example`)

Both are gitignored. The `.example` files are committed.

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

S3 bucket for Terraform remote state. All other stacks store their state here.

**Creates:**
- S3 bucket (versioning, AES256 encryption, public access block, least-privilege bucket policy)

**Setup:**

```bash
cd bootstrap

cp terraform.tfvars.example terraform.tfvars
cp backend.hcl.example backend.hcl
# Edit both — set your account ID and bucket name

# Step 1: comment out `backend "s3" {}` in terraform.tf, then create the bucket (local state)
terraform init
terraform apply

# Step 2: uncomment `backend "s3" {}` in terraform.tf, then migrate state to S3
terraform init -backend-config=backend.hcl -migrate-state
rm terraform.tfstate terraform.tfstate.backup
```

### foundation

Persistent infrastructure that survives app stack destroy/recreate.

**Creates:**
- VPC with 2 public + 2 private subnets, internet gateway, route tables
- NAT gateway (optional, off by default)
- VPC endpoints: S3 (gateway), Secrets Manager + Batch (interface, private DNS)
- 5 security groups: EC2, RDS, Batch, Lambda, VPC endpoints
- 7 IAM roles: EC2 orchestrator, Batch job/execution/instance, Spot Fleet, Lambda execution, Batch service-linked
- 2 instance profiles: EC2 orchestrator, Batch instance
- Prod S3 bucket (prevent_destroy) + test S3 bucket (force_destroy)

**Key outputs** (see `foundation/outputs.tf` for full details):
- Networking: `vpc_id`, `public_subnet_ids`, `private_subnet_ids`
- Security groups: `ec2_security_group_id`, `rds_security_group_id`, `batch_security_group_id`, `lambda_security_group_id`, `vpc_endpoints_security_group_id`
- IAM roles: `ec2_role_arn`, `batch_job_role_arn`, `batch_execution_role_arn`, `batch_instance_role_arn`, `batch_service_role_arn`, `spot_fleet_role_arn`, `lambda_execution_role_arn`
- Instance profiles: `ec2_instance_profile_name`, `batch_instance_profile_name`, `batch_instance_profile_arn`
- S3 buckets: `prod_bucket_name`, `prod_bucket_arn`, `test_bucket_name`, `test_bucket_arn`
- Naming contracts: `ecr_repository_name_prefix`, `batch_log_group_name`, `ec2_log_group_name`, `lambda_function_name`, `lambda_log_group_name`, `rds_secret_name`, `rds_secret_arn_pattern`

**Setup:**

```bash
cd foundation

cp terraform.tfvars.example terraform.tfvars
cp backend.hcl.example backend.hcl
# Edit both — set account ID, bucket name, admin CIDRs

terraform init -backend-config=backend.hcl

# If the account has previously used Batch, import the existing service-linked role:
# terraform import aws_iam_service_linked_role.batch arn:aws:iam::<account_id>:role/aws-service-role/batch.amazonaws.com/AWSServiceRoleForBatch

terraform apply
```

**Required IAM permissions:** `iam:CreateRole`, `iam:PutRolePolicy`, `iam:AttachRolePolicy`, `iam:CreateInstanceProfile`, `iam:AddRoleToInstanceProfile` scoped to `arn:aws:iam::<account_id>:role/<project_name>-*` and `arn:aws:iam::<account_id>:instance-profile/<project_name>-*`.

### app (coming soon)

Application infrastructure — ok to destroy and recreate.

**Will create:**
- EC2 instance (orchestrator) + worker instance(s)
- RDS Postgres (dagster + pipeline databases)
- 4 ECR repos: orchestrator, model-worker, nd-scenario-worker, kwse-scenario-worker
- Batch compute environment (GPU SPOT), job queue, 2 job definitions (nd, kwse)
- Lambda function (Batch completion handler via EventBridge)
- EventBridge rule + target + `aws_lambda_permission`
- 3 CloudWatch log groups: batch, ec2, lambda
