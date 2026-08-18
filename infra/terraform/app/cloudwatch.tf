resource "aws_cloudwatch_log_group" "batch" {
  name              = "/aws/batch/${var.project_name}"
  retention_in_days = var.log_retention_days

  tags = { Name = "${var.project_name}-batch-logs" }
}

resource "aws_cloudwatch_log_group" "ec2" {
  name              = "/aws/ec2/${var.project_name}"
  retention_in_days = var.log_retention_days

  tags = { Name = "${var.project_name}-ec2-logs" }
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.project_name}-batch-handler"
  retention_in_days = var.log_retention_days

  tags = { Name = "${var.project_name}-lambda-logs" }
}
