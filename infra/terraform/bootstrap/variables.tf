variable "allowed_account_id" {
  description = "AWS account ID to restrict operations to — prevents accidental apply in wrong account"
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
}

variable "region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}
