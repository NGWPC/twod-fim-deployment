resource "aws_cloudwatch_log_group" "batch" {
  name              = var.batch_log_group_name
  retention_in_days = var.log_retention_days

  tags = { Name = "${var.project_name}-batch-logs" }
}

resource "aws_cloudwatch_log_group" "ec2" {
  name              = var.ec2_log_group_name
  retention_in_days = var.log_retention_days

  tags = { Name = "${var.project_name}-ec2-logs" }
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = var.lambda_log_group_name
  retention_in_days = var.log_retention_days

  tags = { Name = "${var.project_name}-lambda-logs" }
}
