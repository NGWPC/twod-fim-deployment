# --- EC2 ---

output "orchestrator_instance_id" {
  description = "EC2 orchestrator instance ID"
  value       = aws_instance.orchestrator.id
}

output "orchestrator_private_ip" {
  description = "Private IP of orchestrator (SSH + Dagster UI via bastion/VPN)"
  value       = aws_instance.orchestrator.private_ip
}

output "worker_instance_ids" {
  description = "List of worker EC2 instance IDs (empty if worker_count=0)"
  value       = aws_instance.worker[*].id
}

output "worker_private_ips" {
  description = "List of worker private IPs (empty if worker_count=0)"
  value       = aws_instance.worker[*].private_ip
}

# --- RDS ---

output "rds_endpoint" {
  description = "RDS Postgres endpoint (host:port)"
  value       = aws_db_instance.main.endpoint
}

output "rds_address" {
  description = "RDS Postgres hostname"
  value       = aws_db_instance.main.address
}

output "rds_secret_arn" {
  description = "Secrets Manager secret ARN for RDS connection metadata (host, port, username, database - no password)"
  value       = aws_secretsmanager_secret.rds_credentials.arn
}

output "rds_master_user_secret_arn" {
  description = "AWS-managed Secrets Manager secret ARN holding the RDS master password (manage_master_user_password)"
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}

# --- Container images ---

output "image_repos" {
  description = "Resolved image repositories (ECR URLs when create_ecr = true, external registry when false)"
  value = {
    orchestrator         = local.orchestrator_image_repo
    build_model          = local.build_model_image_repo
    nd_scenario_worker   = local.nd_image_repo
    kwse_scenario_worker = local.kwse_image_repo
  }
}

# --- Batch ---

output "batch_job_queue_name" {
  description = "Batch job queue name (for Dagster sensor)"
  value       = aws_batch_job_queue.scenarios.name
}

output "batch_nd_job_definition_name" {
  description = "ND scenario Batch job definition name"
  value       = aws_batch_job_definition.nd.name
}

output "batch_kwse_job_definition_name" {
  description = "KWSE scenario Batch job definition name"
  value       = aws_batch_job_definition.kwse.name
}

# --- Lambda + EventBridge ---

output "lambda_function_arn" {
  description = "Lambda function ARN"
  value       = aws_lambda_function.batch_handler.arn
}

output "eventbridge_dlq_url" {
  description = "SQS dead-letter queue URL for failed EventBridge deliveries"
  value       = aws_sqs_queue.eventbridge_dlq.url
}
