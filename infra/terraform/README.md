# Terraform Infrastructure

Infrastructure as code for the 2D flood inundation mapping (FIM) pipeline.
Three independent stacks, applied in order: bootstrap, then foundation, then app.
Bootstrap and foundation are **optional** when using existing infrastructure - only the app stack is required.
Destroy in reverse order: app, then foundation, then bootstrap.
When using existing networking and storage, skip bootstrap and foundation entirely.

Each stack reads two local config files that are gitignored and created from committed `.example` templates.

- `terraform.tfvars` - input variables, copied from `terraform.tfvars.example`
- `backend.hcl` - remote state config, copied from `backend.hcl.example`

## Prerequisites

- Terraform >= 1.14, AWS provider ~> 6.0 (pinned per stack).
- AWS CLI with a profile for the target account.
- Permissions to create S3, IAM, VPC, Batch, RDS, ECR, Lambda, and CloudWatch resources.

## Layout

```
infra/terraform/
├── README.md
├── bootstrap/                     state bucket stack, apply once
│   ├── main.tf                    S3 state bucket - versioning, AES256 encryption, public access block, account-restricted TLS-only policy
│   ├── outputs.tf                 bucket_name, bucket_arn, region
│   ├── providers.tf               AWS provider config, default tags (incl. optional team/poc)
│   ├── terraform.tf               version constraints, S3 backend block, first-time setup notes
│   ├── variables.tf               allowed_account_id, project_name, region, team, poc
│   ├── backend.hcl.example        remote state backend template
│   ├── terraform.tfvars.example   input variable template
│   └── .terraform.lock.hcl        provider version lock (generated, committed)
├── foundation/                    persistent infra stack - VPC, endpoints, buckets
│   ├── data.tf                    aws_caller_identity data source
│   ├── networking.tf              VPC, public/private subnets, IGW, NAT gateway, VPC endpoints, vpce SG
│   ├── outputs.tf                 vpc_id, private_subnet_ids, vpce_security_group_id, prod_bucket_name, test_bucket_name, dagster_bucket_name
│   ├── providers.tf               AWS provider config, default tags (incl. optional team/poc)
│   ├── storage.tf                 prod (prevent_destroy), test (force_destroy), and dagster (force_destroy) S3 buckets
│   ├── terraform.tf               version constraints, S3 backend block
│   ├── variables.tf               create_networking, create_storage, CIDRs, existing_* fallbacks, team, poc
│   ├── backend.hcl.example        remote state backend template
│   ├── terraform.tfvars.example   input variable template
│   └── .terraform.lock.hcl        provider version lock (generated, committed)
└── app/                           application stack, ok to destroy and recreate
    ├── batch.tf                   Batch compute environment (GPU SPOT), job queue, nd + kwse job definitions
    ├── cloudwatch.tf              log groups: batch, ec2, lambda
    ├── data.tf                    aws_partition, aws_caller_identity data sources
    ├── ec2.tf                     orchestrator instance, optional worker instances, optional SSH key pair
    ├── ecr.tf                     4 ECR repos (optional, create_ecr toggle) + scan-on-push + lifecycle policies
    ├── iam.tf                     create_iam toggle, IAM roles + instance profiles + SSM policy + conditional ECR policies, existing_* fallbacks
    ├── lambda.tf                  Batch completion handler Lambda (placeholder logic), EventBridge rule/target, SQS dead-letter queue
    ├── outputs.tf                 EC2, RDS, ECR, Batch, and Lambda outputs
    ├── providers.tf               AWS provider config, default tags (incl. optional team/poc)
    ├── rds.tf                     RDS Postgres instance, DB subnet group, Secrets Manager secret (connection metadata)
    ├── security_groups.tf         EC2, RDS, Batch, Lambda security groups + VPC endpoint ingress rules
    ├── terraform.tf               version constraints (aws, archive), S3 backend block
    ├── variables.tf               shared + foundation-input + container registry + app-specific variables
    ├── backend.hcl.example        remote state backend template
    ├── terraform.tfvars.example   input variable template
    └── .terraform.lock.hcl        provider version lock (generated, committed)
```

## Stacks

### Bootstrap

Creates the S3 bucket that holds Terraform remote state for the other two stacks.
Apply once per AWS account.

Bootstrap is **optional**.
If you have an existing S3 bucket for state storage, skip this stack and set `bucket` in foundation and app `backend.hcl` to that bucket name.

### Foundation

Persistent infrastructure that survives an app stack destroy/recreate.
**Optional** when using existing networking and storage - set both toggles to false, or skip this stack entirely and pass values directly to the app stack.

Creates:
- VPC with public subnets (NAT gateway placement only, no workloads) and private subnets (all workloads)
- NAT gateway for private subnet internet access
- VPC endpoints for S3, Secrets Manager, and Batch
- VPC endpoint security group (app stack adds ingress rules when `vpce_security_group_id` is provided)
- Prod, test, and Dagster compute logs S3 buckets

Networking and storage are each independently toggleable via `create_networking` and `create_storage`.

### App

Application infrastructure, safe to destroy and recreate.

Creates:
- EC2 orchestrator and optional worker instances (Ubuntu Noble, private subnet, IMDSv2, SSM-ready)
- RDS Postgres with AWS-managed master password
- AWS Batch GPU SPOT compute environment, job queue, nd + kwse job definitions
- 4 ECR repos with scan-on-push and lifecycle policies (optional, gated by `create_ecr`)
- Lambda batch-completion handler wired to EventBridge with SQS dead-letter queue
- CloudWatch log groups
- 6 IAM roles + 2 instance profiles (scoped to actual resource ARNs), optional SSM policy attachment
- 4 security groups (EC2, RDS, Batch, Lambda) + conditional VPC endpoint ingress rules

IAM is toggleable via `create_iam`.
ECR is toggleable via `create_ecr` - set to `false` when using an external registry like GHCR.
When `create_ecr = false`, image repositories are provided via `orchestrator_image_repo`, `build_model_image_repo`, `nd_image_repo`, and `kwse_image_repo`.
Security groups are always created by this stack.

## Tags

All three stacks apply default tags to every resource:

| Tag | Source | Required |
|---|---|---|
| `ManagedBy` | hardcoded `"Terraform"` | Always |
| `Project` | `var.project_name` | Always |
| `Stack` | hardcoded per stack | Always |
| `Team` | `var.team` | Optional (omitted if empty) |
| `POC` | `var.poc` | Optional (omitted if empty) |

Set `team` and `poc` in `terraform.tfvars` if required by your organization.

## Toggles

| Toggle | Stack | Default | Controls | `existing_*` fallback variables required when false |
|---|---|---|---|---|
| `create_networking` | foundation | `true` | VPC, subnets, IGW, NAT gateway, VPC endpoints, VPC endpoints security group | `existing_vpc_id`, `existing_private_subnet_ids` (+ optionally `existing_vpce_security_group_id`) |
| `create_storage` | foundation | `true` | Prod, test, and Dagster compute logs S3 buckets | `existing_prod_bucket_name`, `existing_test_bucket_name`, `existing_dagster_bucket_name` |
| `create_iam` | app | `true` | 6 IAM roles + 2 instance profiles (EC2 orchestrator, Batch job/execution/instance, Spot Fleet, Lambda execution) | `existing_ec2_instance_profile_name`, `existing_batch_job_role_arn`, `existing_batch_execution_role_arn`, `existing_batch_instance_profile_arn`, `existing_spot_fleet_role_arn`, `existing_lambda_execution_role_arn`, `existing_batch_service_role_arn` |
| `create_ecr` | app | `true` | 4 ECR repos (orchestrator, model_worker, nd, kwse) + lifecycle policies + ECR IAM policies | `orchestrator_image_repo`, `build_model_image_repo`, `nd_image_repo`, `kwse_image_repo` |
| `create_batch_service_linked_role` | app | `true` | The account-global `AWSServiceRoleForBatch` service-linked role | none directly - see note below |

`create_batch_service_linked_role` is a 3-way switch layered on top of `create_iam`.
`create_iam = false` always uses `existing_batch_service_role_arn`, regardless of this toggle.
`create_iam = true` with `create_batch_service_linked_role = false` reuses the account's existing service-linked role by its well-known ARN instead of creating it, since a service-linked role can only be created once per account.

## Fresh deployment

Set the AWS profile and confirm the account ID before touching any stack.
Bootstrap and foundation are optional. If using existing networking and storage, skip to step 3 (App).

```bash
export AWS_PROFILE=<your-profile>
aws sts get-caller-identity --query Account --output text
```

### 1. Bootstrap

```bash
cd infra/terraform/bootstrap
cp terraform.tfvars.example terraform.tfvars
cp backend.hcl.example backend.hcl
# edit both: account ID, state bucket name

# Step 1: comment out the line `backend "s3" {}` in terraform.tf (line 19)
# so Terraform uses local state for the first apply
terraform init
terraform apply

# Step 2: uncomment `backend "s3" {}` in terraform.tf
# then migrate the local state into the S3 bucket you just created
terraform init -backend-config=backend.hcl -migrate-state

# Step 3: delete the local state files (state now lives in S3)
rm terraform.tfstate terraform.tfstate.backup
```

### 2. Foundation

```bash
cd ../foundation
cp terraform.tfvars.example terraform.tfvars
cp backend.hcl.example backend.hcl
# edit both: account ID, state bucket name

terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```

Only `allowed_account_id` is required; everything else has a default.

### 3. App

```bash
cd ../app
cp terraform.tfvars.example terraform.tfvars
cp backend.hcl.example backend.hcl

# If foundation was deployed, pull its outputs into terraform.tfvars:
#   terraform -chdir=../foundation output
# If using existing infra, get these existing values from your environment:
#   vpc_id, private_subnet_ids, prod_bucket_name, test_bucket_name, dagster_s3_bucket

# also set: ec2_ami_id, dagster_s3_bucket, nd_image_tag, kwse_image_tag
# optional: ssm_logging_policy_arn, ec2_ssh_public_key, allowed_admin_cidrs
# when create_ecr = false: orchestrator_image_repo, build_model_image_repo, nd_image_repo, kwse_image_repo
terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```

If the account already has the Batch service-linked role from prior use, set `create_batch_service_linked_role = false` instead of importing it.

After `terraform apply`, set up databases and deploy services using the scripts in `deploy/`:

```bash
# On EC2 (via SSM), from the cloned repo with .env created from example.cloud.env:
python deploy/init_db.py          # create databases, users, schema
python deploy/deploy.py           # pull images, start Dagster services
python deploy/init_db.py --reset  # clean slate (drop + recreate)
```

## Enterprise mode

Set every toggle to false: `create_networking`, `create_storage`, `create_iam`, `create_batch_service_linked_role`, `create_ecr`.
Provide the matching `existing_*` values for each.
Foundation then creates nothing and passes the existing VPC, subnets, endpoint security group, and buckets straight through its outputs.
App creates no IAM roles or instance profiles either, resolving them from the `existing_*` ARNs instead.
App still always creates its own security groups (EC2, RDS, Batch, Lambda), the EC2 instances, RDS, Batch resources, and Lambda. ECR repos are conditional (`create_ecr`).
IAM, ECR, and the resources gated by foundation's toggles can be skipped.

## Foundation outputs

| Output | Provides |
|---|---|
| `vpc_id` | VPC ID - created, or the existing VPC ID passed through |
| `private_subnet_ids` | Private subnet IDs for all workloads - created, or existing IDs passed through |
| `vpce_security_group_id` | VPC interface endpoints security group ID - created, or existing SG passed through |
| `prod_bucket_name` | Prod artifact S3 bucket name - created, or existing name passed through |
| `test_bucket_name` | Test artifact S3 bucket name - created, or existing name passed through |
| `dagster_bucket_name` | Dagster compute logs S3 bucket name - created, or existing name passed through |

## App outputs

| Output | Provides |
|---|---|
| `orchestrator_instance_id` | EC2 orchestrator instance ID |
| `orchestrator_private_ip` | Orchestrator private IP (SSH + Dagster UI via bastion/VPN) |
| `worker_instance_ids` | Worker instance IDs (empty if `worker_count = 0`) |
| `worker_private_ips` | Worker private IPs (empty if `worker_count = 0`) |
| `rds_endpoint` | RDS Postgres endpoint (host:port) |
| `rds_address` | RDS Postgres hostname |
| `rds_secret_arn` | Secrets Manager secret ARN for connection metadata (no password) |
| `rds_master_user_secret_arn` | AWS-managed secret ARN holding the RDS master password |
| `image_repos` | Resolved image repositories (ECR URLs when create_ecr = true, external registry URLs when false) |
| `batch_job_queue_name` | Batch job queue name (for the Dagster sensor) |
| `batch_nd_job_definition_name` | ND scenario Batch job definition name |
| `batch_kwse_job_definition_name` | KWSE scenario Batch job definition name |
| `lambda_function_arn` | Batch completion handler Lambda ARN |
| `eventbridge_dlq_url` | SQS dead-letter queue URL for failed EventBridge deliveries |

## Deferred / TODO

- Batch compute nodes boot the AWS-managed ECS-optimized AMI, not the Ubuntu Noble golden AMI used for the EC2 orchestrator and workers.
If Batch hosts also need Noble, a custom AMI with ECS agent, Docker, NVIDIA drivers, and GPU runtime is required.
- EC2 instances have no public IP.
SSM Session Manager is supported via `ssm_logging_policy_arn` (attaches the required policy to the EC2 role).
SSH access is optional via `ec2_ssh_public_key` and `allowed_admin_cidrs`.
- Dagster UI (port 3000) is accessible via SSM port forwarding or directly from a network with a route to private subnets (e.g. AWS Workspace) when `allowed_admin_cidrs` includes the source CIDR.
- Additional VPC endpoints (ECR, ECS, CloudWatch Logs) would be needed if `enable_nat_gateway` is disabled.
- Worker EC2 instances reuse the orchestrator's IAM role; there is no separate, scoped-down worker role yet.
- Single-AZ NAT gateway is a sandbox cost optimization.
Production should have one NAT per AZ for resilience.
- Any other items that may have been missed.
