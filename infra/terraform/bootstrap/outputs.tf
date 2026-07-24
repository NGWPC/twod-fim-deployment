output "bucket_name" {
  description = "S3 bucket name for Terraform state - use in foundation/ and app/ backend config"
  value       = aws_s3_bucket.state.id
}

output "bucket_arn" {
  description = "S3 bucket ARN for Terraform state"
  value       = aws_s3_bucket.state.arn
}

output "region" {
  description = "AWS region - use in foundation/ and app/ backend config"
  value       = var.region
}
