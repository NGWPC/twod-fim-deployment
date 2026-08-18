data "archive_file" "lambda_placeholder" {
  type        = "zip"
  output_path = "${path.module}/lambda_placeholder.zip"

  source {
    content  = <<-PYTHON
      def lambda_handler(event, context):
          print("Batch completion event received")
          print(event)
          return {"statusCode": 200}
    PYTHON
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "batch_handler" {
  function_name    = "${var.project_name}-batch-handler"
  role             = local.lambda_execution_role_arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory_size
  filename         = data.archive_file.lambda_placeholder.output_path
  source_code_hash = data.archive_file.lambda_placeholder.output_base64sha256

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  depends_on = [aws_cloudwatch_log_group.lambda, aws_iam_role_policy_attachment.lambda_vpc_access]

  tags = { Name = "${var.project_name}-batch-handler" }
}

# --- EventBridge ---

resource "aws_cloudwatch_event_rule" "batch_state_change" {
  name = "${var.project_name}-batch-state-change"

  event_pattern = jsonencode({
    source      = ["aws.batch"]
    detail-type = ["Batch Job State Change"]
    detail = {
      status   = ["SUCCEEDED", "FAILED"]
      jobQueue = [aws_batch_job_queue.scenarios.arn]
    }
  })

  tags = { Name = "${var.project_name}-batch-state-change" }
}

resource "aws_cloudwatch_event_target" "batch_to_lambda" {
  rule = aws_cloudwatch_event_rule.batch_state_change.name
  arn  = aws_lambda_function.batch_handler.arn

  dead_letter_config {
    arn = aws_sqs_queue.eventbridge_dlq.arn
  }

  depends_on = [aws_lambda_permission.eventbridge, aws_sqs_queue_policy.eventbridge_dlq]
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.batch_handler.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.batch_state_change.arn
}

# --- SQS Dead-Letter Queue ---

resource "aws_sqs_queue" "eventbridge_dlq" {
  name                    = "${var.project_name}-eventbridge-dlq"
  sqs_managed_sse_enabled = true

  tags = { Name = "${var.project_name}-eventbridge-dlq" }
}

resource "aws_sqs_queue_policy" "eventbridge_dlq" {
  queue_url = aws_sqs_queue.eventbridge_dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowEventBridgeSendMessage"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.eventbridge_dlq.arn
      Condition = {
        ArnEquals = {
          "aws:SourceArn" = aws_cloudwatch_event_rule.batch_state_change.arn
        }
      }
    }]
  })
}
