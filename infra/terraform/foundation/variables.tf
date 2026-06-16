variable "allowed_account_id" {
  description = "AWS account ID to restrict operations to — prevents accidental apply in wrong account"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.allowed_account_id))
    error_message = "allowed_account_id must be a 12-digit AWS account ID."
  }
}

variable "allowed_admin_cidrs" {
  description = "CIDR blocks allowed for SSH (:22) and Dagster UI (:3000) ingress"
  type        = list(string)

  validation {
    condition     = length(var.allowed_admin_cidrs) > 0
    error_message = "At least one admin CIDR is required."
  }

  validation {
    condition     = alltrue([for c in var.allowed_admin_cidrs : can(cidrhost(c, 0))])
    error_message = "All allowed_admin_cidrs must be valid CIDR notation."
  }
}

variable "enable_nat_gateway" {
  description = "Create NAT gateway for private subnet internet access (adds ongoing cost)"
  type        = bool
  default     = false
}

variable "private_subnet_cidrs" {
  description = "Private subnet CIDRs (one per AZ, for RDS and Lambda)"
  type        = list(string)
  default     = ["10.0.101.0/24", "10.0.102.0/24"]

  validation {
    condition     = length(var.private_subnet_cidrs) == 2
    error_message = "Exactly 2 private subnet CIDRs required (one per AZ)."
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
  description = "Public subnet CIDRs (one per AZ, for EC2 and Batch)"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]

  validation {
    condition     = length(var.public_subnet_cidrs) == 2
    error_message = "Exactly 2 public subnet CIDRs required (one per AZ)."
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

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be valid CIDR notation."
  }
}
