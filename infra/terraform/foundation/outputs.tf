# --- Networking ---

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "List of public subnet IDs"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "List of private subnet IDs"
  value       = aws_subnet.private[*].id
}

# --- Security groups ---

output "ec2_security_group_id" {
  description = "EC2 orchestrator security group ID"
  value       = aws_security_group.ec2.id
}

output "rds_security_group_id" {
  description = "RDS security group ID"
  value       = aws_security_group.rds.id
}

output "batch_security_group_id" {
  description = "Batch compute security group ID"
  value       = aws_security_group.batch.id
}

output "lambda_security_group_id" {
  description = "Lambda security group ID"
  value       = aws_security_group.lambda.id
}

output "vpc_endpoints_security_group_id" {
  description = "VPC interface endpoints security group ID"
  value       = aws_security_group.vpc_endpoints.id
}

# --- IAM roles + instance profiles ---

output "ec2_instance_profile_name" {
  description = "EC2 orchestrator instance profile name"
  value       = aws_iam_instance_profile.ec2_orchestrator.name
}

output "ec2_role_arn" {
  description = "EC2 orchestrator IAM role ARN"
  value       = aws_iam_role.ec2_orchestrator.arn
}

output "batch_job_role_arn" {
  description = "Batch job IAM role ARN (application permissions)"
  value       = aws_iam_role.batch_job.arn
}

output "batch_execution_role_arn" {
  description = "Batch ECS execution role ARN (ECR pull + log shipping)"
  value       = aws_iam_role.batch_execution.arn
}

output "batch_instance_profile_name" {
  description = "Batch ECS container instance profile name"
  value       = aws_iam_instance_profile.batch_instance.name
}

output "batch_instance_profile_arn" {
  description = "Batch ECS container instance profile ARN"
  value       = aws_iam_instance_profile.batch_instance.arn
}

output "batch_instance_role_arn" {
  description = "Batch container instance IAM role ARN"
  value       = aws_iam_role.batch_instance.arn
}

output "batch_service_role_arn" {
  description = "Batch service-linked role ARN"
  value       = aws_iam_service_linked_role.batch.arn
}

output "spot_fleet_role_arn" {
  description = "Spot Fleet IAM role ARN"
  value       = aws_iam_role.spot_fleet.arn
}

output "lambda_execution_role_arn" {
  description = "Lambda execution role ARN"
  value       = aws_iam_role.lambda_execution.arn
}

# --- S3 buckets ---

output "prod_bucket_name" {
  description = "Prod artifact S3 bucket name"
  value       = aws_s3_bucket.prod.id
}

output "prod_bucket_arn" {
  description = "Prod artifact S3 bucket ARN"
  value       = aws_s3_bucket.prod.arn
}

output "test_bucket_name" {
  description = "Test artifact S3 bucket name"
  value       = aws_s3_bucket.test.id
}

output "test_bucket_arn" {
  description = "Test artifact S3 bucket ARN"
  value       = aws_s3_bucket.test.arn
}

# --- Naming contracts (app stack must use these exact names) ---

output "rds_secret_name" {
  description = "Secrets Manager secret name for RDS credentials — app creates with this name"
  value       = local.rds_secret_name
}

output "rds_secret_arn_pattern" {
  description = "IAM-compatible ARN pattern for the RDS secret"
  value       = local.rds_secret_arn_pattern
}

output "ecr_repository_name_prefix" {
  description = "ECR repo name prefix — app creates repos with this prefix"
  value       = "${var.project_name}-"
}

output "batch_log_group_name" {
  description = "Expected Batch CloudWatch log group name — app creates with this name"
  value       = "/aws/batch/${var.project_name}"
}

output "ec2_log_group_name" {
  description = "Expected EC2 CloudWatch log group name — app creates with this name"
  value       = "/aws/ec2/${var.project_name}"
}

output "lambda_function_name" {
  description = "Expected Lambda function name — app creates with this name"
  value       = local.lambda_function_name
}

output "lambda_log_group_name" {
  description = "Expected Lambda CloudWatch log group name — app creates with this name"
  value       = "/aws/lambda/${local.lambda_function_name}"
}
