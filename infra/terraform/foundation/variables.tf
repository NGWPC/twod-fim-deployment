variable "allowed_account_id" {
  description = "AWS account ID to restrict operations to - prevents accidental apply in wrong account"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.allowed_account_id))
    error_message = "allowed_account_id must be a 12-digit AWS account ID."
  }
}

variable "create_networking" {
  description = "Create a VPC with public and private subnets. Set false to reference an existing VPC via existing_* variables."
  type        = bool
  default     = true
}

variable "create_storage" {
  description = "Create the prod and test artifact S3 buckets. Set false to reference existing buckets via existing_* variables."
  type        = bool
  default     = true
}

variable "enable_nat_gateway" {
  description = "Create NAT gateway for private subnet internet access (adds ongoing cost)"
  type        = bool
  default     = true
}

variable "existing_private_subnet_ids" {
  description = "Existing private subnet IDs for all workloads (required, min 2, when create_networking = false)"
  type        = list(string)
  default     = []

  validation {
    condition     = var.create_networking || length(var.existing_private_subnet_ids) >= 2
    error_message = "existing_private_subnet_ids requires at least 2 subnet IDs when create_networking = false."
  }

  validation {
    condition     = alltrue([for s in var.existing_private_subnet_ids : can(regex("^subnet-", s))])
    error_message = "every existing_private_subnet_ids entry must start with 'subnet-'."
  }
}

variable "existing_prod_bucket_name" {
  description = "Existing prod artifact S3 bucket name (required when create_storage = false)"
  type        = string
  default     = ""

  validation {
    condition     = var.create_storage || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.existing_prod_bucket_name))
    error_message = "existing_prod_bucket_name is required (and must be a valid S3 bucket name) when create_storage = false."
  }
}

variable "existing_test_bucket_name" {
  description = "Existing test artifact S3 bucket name (required when create_storage = false)"
  type        = string
  default     = ""

  validation {
    condition     = var.create_storage || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.existing_test_bucket_name))
    error_message = "existing_test_bucket_name is required (and must be a valid S3 bucket name) when create_storage = false."
  }
}

variable "existing_vpc_id" {
  description = "Existing VPC ID (required when create_networking = false)"
  type        = string
  default     = ""

  validation {
    condition     = var.create_networking || can(regex("^vpc-", var.existing_vpc_id))
    error_message = "existing_vpc_id is required (and must start with 'vpc-') when create_networking = false."
  }
}

variable "existing_vpce_security_group_id" {
  description = "Existing VPC interface endpoints security group ID (required when create_networking = false)"
  type        = string
  default     = ""

  validation {
    condition     = var.create_networking || can(regex("^sg-", var.existing_vpce_security_group_id))
    error_message = "existing_vpce_security_group_id is required (and must start with 'sg-') when create_networking = false."
  }
}

variable "private_subnet_cidrs" {
  description = "Private subnet CIDRs (one per AZ, for all workloads)"
  type        = list(string)
  default     = ["10.0.3.0/24", "10.0.4.0/24"]

  validation {
    condition     = length(var.private_subnet_cidrs) >= 2
    error_message = "At least 2 private subnet CIDRs required (multi-AZ)."
  }

  validation {
    condition     = alltrue([for c in var.private_subnet_cidrs : can(cidrhost(c, 0))])
    error_message = "All private_subnet_cidrs must be valid CIDR notation."
  }
}

variable "prod_bucket_name" {
  description = "Override prod artifact bucket name (default: {project_name}-prod-{account_id})"
  type        = string
  default     = ""

  validation {
    condition     = var.prod_bucket_name == "" || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.prod_bucket_name))
    error_message = "prod_bucket_name must be a valid S3 bucket name (lowercase, 3-63 chars)."
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

variable "public_subnet_cidrs" {
  description = "Public subnet CIDRs (one per AZ, for NAT gateway placement only - no workloads)"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]

  validation {
    condition     = length(var.public_subnet_cidrs) >= 1
    error_message = "At least 1 public subnet CIDR required."
  }

  validation {
    condition     = alltrue([for c in var.public_subnet_cidrs : can(cidrhost(c, 0))])
    error_message = "All public_subnet_cidrs must be valid CIDR notation."
  }
}

variable "region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "test_bucket_name" {
  description = "Override test artifact bucket name (default: {project_name}-test-{account_id})"
  type        = string
  default     = ""

  validation {
    condition     = var.test_bucket_name == "" || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.test_bucket_name))
    error_message = "test_bucket_name must be a valid S3 bucket name (lowercase, 3-63 chars)."
  }
}

variable "team" {
  description = "Team name for cost-allocation and ownership tagging (omitted from tags if empty)"
  type        = string
  default     = ""
}

variable "poc" {
  description = "Point of contact for these resources (omitted from tags if empty)"
  type        = string
  default     = ""
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be valid CIDR notation."
  }
}
