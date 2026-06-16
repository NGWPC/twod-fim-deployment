# --- EC2 ---

output "orchestrator_public_ip" {
  description = "Public IP of orchestrator (SSH + Dagster UI)"
  value       = aws_instance.orchestrator.public_ip
}

output "orchestrator_instance_id" {
  description = "EC2 orchestrator instance ID"
  value       = aws_instance.orchestrator.id
}

output "worker_public_ips" {
  description = "List of worker public IPs (empty if worker_count=0)"
  value       = aws_instance.worker[*].public_ip
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
  description = "Secrets Manager secret ARN for RDS credentials"
  value       = aws_secretsmanager_secret.rds_credentials.arn
}

# --- ECR ---

output "ecr_repository_urls" {
  description = "ECR repository URLs (docker push targets)"
  value = {
    orchestrator         = aws_ecr_repository.orchestrator.repository_url
    model_worker         = aws_ecr_repository.model_worker.repository_url
    nd_scenario_worker   = aws_ecr_repository.nd_scenario_worker.repository_url
    kwse_scenario_worker = aws_ecr_repository.kwse_scenario_worker.repository_url
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
