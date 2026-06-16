# --- Shared (same values across all stacks) ---

variable "allowed_account_id" {
  description = "AWS account ID — prevents accidental apply in wrong account"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.allowed_account_id))
    error_message = "allowed_account_id must be a 12-digit AWS account ID."
  }
}

variable "project_name" {
  description = "Project name used as prefix for resource names"
  type        = string
  default     = "twod-fim"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]*[a-z0-9]$", var.project_name))
    error_message = "project_name must be lowercase letters, digits, and hyphens only."
  }
}

variable "region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

# --- Foundation inputs (from `terraform output` on foundation stack) ---

variable "batch_execution_role_arn" {
  description = "Batch ECS execution role ARN (ECR pull + log shipping)"
  type        = string
}

variable "batch_instance_profile_arn" {
  description = "Batch ECS container instance profile ARN"
  type        = string
}

variable "batch_job_role_arn" {
  description = "Batch job IAM role ARN (S3 data access)"
  type        = string
}

variable "batch_log_group_name" {
  description = "CloudWatch log group name for Batch jobs"
  type        = string
}

variable "batch_security_group_id" {
  description = "Batch compute security group ID"
  type        = string
}

variable "batch_service_role_arn" {
  description = "Batch service-linked role ARN"
  type        = string
}

variable "ec2_instance_profile_name" {
  description = "EC2 orchestrator instance profile name"
  type        = string
}

variable "ec2_log_group_name" {
  description = "CloudWatch log group name for EC2"
  type        = string
}

variable "ec2_security_group_id" {
  description = "EC2 orchestrator security group ID"
  type        = string
}

variable "ecr_repository_name_prefix" {
  description = "ECR repo name prefix from foundation"
  type        = string
}

variable "lambda_execution_role_arn" {
  description = "Lambda execution role ARN"
  type        = string
}

variable "lambda_function_name" {
  description = "Lambda function name (from foundation naming contract)"
  type        = string
}

variable "lambda_log_group_name" {
  description = "CloudWatch log group name for Lambda"
  type        = string
}

variable "lambda_security_group_id" {
  description = "Lambda security group ID"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs (for RDS, Lambda)"
  type        = list(string)

  validation {
    condition     = length(var.private_subnet_ids) >= 2
    error_message = "At least 2 private subnet IDs required (RDS multi-AZ)."
  }
}

variable "prod_bucket_name" {
  description = "Prod artifact S3 bucket name"
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnet IDs (for EC2, Batch)"
  type        = list(string)

  validation {
    condition     = length(var.public_subnet_ids) >= 2
    error_message = "At least 2 public subnet IDs required (multi-AZ)."
  }
}

variable "rds_secret_name" {
  description = "Secrets Manager secret name for RDS credentials"
  type        = string
}

variable "rds_security_group_id" {
  description = "RDS security group ID"
  type        = string
}

variable "spot_fleet_role_arn" {
  description = "Spot Fleet IAM role ARN"
  type        = string
}

variable "test_bucket_name" {
  description = "Test artifact S3 bucket name"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID (reserved for future use)"
  type        = string
}

# --- App-specific: Batch ---

variable "batch_instance_types" {
  description = "GPU instance types for Batch compute environment"
  type        = list(string)
  default     = ["g4dn.xlarge"]

  validation {
    condition     = length(var.batch_instance_types) > 0
    error_message = "At least one Batch instance type is required."
  }
}

variable "batch_max_vcpus" {
  description = "Max vCPUs for Batch compute environment"
  type        = number
  default     = 256

  validation {
    condition     = var.batch_max_vcpus > 0
    error_message = "batch_max_vcpus must be positive."
  }
}

variable "batch_retry_attempts" {
  description = "Retry attempts for Batch jobs (handles SPOT interruptions)"
  type        = number
  default     = 3

  validation {
    condition     = var.batch_retry_attempts >= 1 && var.batch_retry_attempts <= 10
    error_message = "batch_retry_attempts must be 1-10 (AWS limit)."
  }
}

variable "batch_shared_memory_size" {
  description = "Shared memory (MB) for GPU containers"
  type        = number
  default     = 4096

  validation {
    condition     = var.batch_shared_memory_size > 0
    error_message = "batch_shared_memory_size must be positive."
  }
}

# --- App-specific: EC2 ---

variable "ec2_instance_type" {
  description = "EC2 orchestrator instance type"
  type        = string
  default     = "t3.medium"
}

variable "ec2_root_volume_size" {
  description = "EC2 root volume size (GB)"
  type        = number
  default     = 30

  validation {
    condition     = var.ec2_root_volume_size >= 8 && var.ec2_root_volume_size <= 1000
    error_message = "ec2_root_volume_size must be 8-1000 GB (guardrail)."
  }
}

variable "ec2_ssh_public_key" {
  description = "SSH public key for EC2 access"
  type        = string
}

# --- App-specific: ECR ---

variable "ecr_force_delete" {
  description = "Allow ECR repos to be deleted with images (true for test)"
  type        = bool
  default     = true
}

variable "ecr_image_tag_mutability" {
  description = "ECR image tag mutability (IMMUTABLE prevents tag reuse — prod safe)"
  type        = string
  default     = "IMMUTABLE"

  validation {
    condition     = contains(["MUTABLE", "IMMUTABLE"], var.ecr_image_tag_mutability)
    error_message = "ecr_image_tag_mutability must be MUTABLE or IMMUTABLE."
  }
}

# --- App-specific: CloudWatch ---

variable "log_retention_days" {
  description = "CloudWatch log retention (days) for all log groups"
  type        = number
  default     = 30

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653, 0], var.log_retention_days)
    error_message = "log_retention_days must be an AWS-supported value (AWS limit): 0/1/3/5/7/14/30/60/90/120/150/180/365/400/545/731/1096/1827/2192/2557/2922/3288/3653."
  }
}

# --- App-specific: Job tuning (ND) ---

variable "nd_image_tag" {
  description = "Docker image tag for nd-scenario-worker (use git SHA or release tag)"
  type        = string
}

variable "nd_job_memory" {
  description = "Memory (MB) for nd job container"
  type        = number
  default     = 15000

  validation {
    condition     = var.nd_job_memory >= 512
    error_message = "nd_job_memory must be at least 512 MB (guardrail)."
  }
}

variable "nd_job_timeout_seconds" {
  description = "Max wall-clock seconds for nd jobs"
  type        = number
  default     = 14400

  validation {
    condition     = var.nd_job_timeout_seconds >= 60
    error_message = "nd_job_timeout_seconds must be at least 60 (guardrail)."
  }
}

variable "nd_job_vcpus" {
  description = "vCPUs for nd job container"
  type        = number
  default     = 4

  validation {
    condition     = var.nd_job_vcpus >= 1
    error_message = "nd_job_vcpus must be at least 1 (guardrail)."
  }
}

# --- App-specific: Job tuning (KWSE) ---

variable "kwse_image_tag" {
  description = "Docker image tag for kwse-scenario-worker (use git SHA or release tag)"
  type        = string
}

variable "kwse_job_memory" {
  description = "Memory (MB) for kwse job container"
  type        = number
  default     = 15000

  validation {
    condition     = var.kwse_job_memory >= 512
    error_message = "kwse_job_memory must be at least 512 MB."
  }
}

variable "kwse_job_timeout_seconds" {
  description = "Max wall-clock seconds for kwse jobs"
  type        = number
  default     = 14400

  validation {
    condition     = var.kwse_job_timeout_seconds >= 60
    error_message = "kwse_job_timeout_seconds must be at least 60."
  }
}

variable "kwse_job_vcpus" {
  description = "vCPUs for kwse job container"
  type        = number
  default     = 4

  validation {
    condition     = var.kwse_job_vcpus >= 1
    error_message = "kwse_job_vcpus must be at least 1."
  }
}

# --- App-specific: Lambda ---

variable "lambda_memory_size" {
  description = "Lambda memory (MB)"
  type        = number
  default     = 128

  validation {
    condition     = var.lambda_memory_size >= 128 && var.lambda_memory_size <= 10240
    error_message = "lambda_memory_size must be 128-10240 MB (AWS limit)."
  }
}

variable "lambda_timeout" {
  description = "Lambda timeout (seconds)"
  type        = number
  default     = 60

  validation {
    condition     = var.lambda_timeout >= 1 && var.lambda_timeout <= 900
    error_message = "lambda_timeout must be 1-900 seconds (AWS limit)."
  }
}

# --- App-specific: RDS ---

variable "rds_allocated_storage" {
  description = "RDS storage (GB)"
  type        = number
  default     = 20

  validation {
    condition     = var.rds_allocated_storage >= 20 && var.rds_allocated_storage <= 1000
    error_message = "rds_allocated_storage must be 20-1000 GB (guardrail, AWS allows up to 64 TB)."
  }
}

variable "rds_backup_retention_days" {
  description = "RDS automated backup retention (days)"
  type        = number
  default     = 7

  validation {
    condition     = var.rds_backup_retention_days >= 0 && var.rds_backup_retention_days <= 35
    error_message = "rds_backup_retention_days must be 0-35 (AWS limit)."
  }
}

variable "rds_engine_version" {
  description = "Postgres major version"
  type        = string
  default     = "16"
}

variable "rds_final_snapshot_identifier" {
  description = "RDS final snapshot identifier (required when rds_skip_final_snapshot is false — must be unique)"
  type        = string
  default     = null
}

variable "rds_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.micro"
}

variable "rds_master_username" {
  description = "RDS master username"
  type        = string
  default     = "dagster_admin"
}

variable "rds_secret_recovery_window_days" {
  description = "Secrets Manager recovery window (days). 0 for test (immediate delete), 7-30 for prod."
  type        = number
  default     = 0

  validation {
    condition     = var.rds_secret_recovery_window_days == 0 || (var.rds_secret_recovery_window_days >= 7 && var.rds_secret_recovery_window_days <= 30)
    error_message = "rds_secret_recovery_window_days must be 0 (immediate) or 7-30."
  }
}

variable "rds_skip_final_snapshot" {
  description = "Skip final snapshot on RDS destroy (true for test, false for prod)"
  type        = bool
  default     = true
}

# --- App-specific: Workers ---

variable "worker_count" {
  description = "Number of additional EC2 worker instances for build_model at scale (0 = none)"
  type        = number
  default     = 0

  validation {
    condition     = var.worker_count >= 0 && var.worker_count <= 10
    error_message = "worker_count must be 0-10 (guardrail, adjust if more needed)."
  }
}
