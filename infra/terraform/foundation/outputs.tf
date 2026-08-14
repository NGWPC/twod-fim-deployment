# Foundation only exports networking IDs and bucket names. IAM roles, app security
# groups, and app naming contracts live in the app stack (see app/outputs.tf).

# --- Networking ---

output "vpc_id" {
  description = "VPC ID (created, or the existing VPC ID passed in)"
  value       = var.create_networking ? aws_vpc.main[0].id : var.existing_vpc_id
}

output "private_subnet_ids" {
  description = "Private subnet IDs for all workloads (created, or the existing subnet IDs passed in)"
  value       = var.create_networking ? aws_subnet.private[*].id : var.existing_private_subnet_ids
}

output "vpce_security_group_id" {
  description = "VPC interface endpoints security group ID (created, or the existing SG ID passed in)"
  value       = var.create_networking ? aws_security_group.vpc_endpoints[0].id : var.existing_vpce_security_group_id
}

# --- Storage ---

output "prod_bucket_name" {
  description = "Prod artifact S3 bucket name (created, or the existing bucket name passed in)"
  value       = var.create_storage ? aws_s3_bucket.prod[0].bucket : var.existing_prod_bucket_name
}

output "test_bucket_name" {
  description = "Test artifact S3 bucket name (created, or the existing bucket name passed in)"
  value       = var.create_storage ? aws_s3_bucket.test[0].bucket : var.existing_test_bucket_name
}

output "dagster_bucket_name" {
  description = "Dagster compute logs S3 bucket name (created, or the existing bucket name passed in)"
  value       = var.create_storage ? aws_s3_bucket.dagster[0].bucket : var.existing_dagster_bucket_name
}
