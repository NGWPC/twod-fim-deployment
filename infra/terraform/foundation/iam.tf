data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  artifact_bucket_arns_objects = [
    "${aws_s3_bucket.prod.arn}/*",
    "${aws_s3_bucket.test.arn}/*",
  ]

  artifact_bucket_arns_list = [
    aws_s3_bucket.prod.arn,
    aws_s3_bucket.test.arn,
  ]

  ecr_repo_arn_pattern = "arn:aws:ecr:${var.region}:${local.account_id}:repository/${var.project_name}-*"

  ec2_log_group_arn = "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/ec2/${var.project_name}:*"

  batch_log_group_arn = "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/batch/${var.project_name}:*"

  rds_secret_name        = "${var.project_name}/rds-credentials"
  rds_secret_arn_pattern = "arn:aws:secretsmanager:${var.region}:${local.account_id}:secret:${local.rds_secret_name}-*"
  lambda_function_name   = "${var.project_name}-batch-handler"
}

# --- EC2 orchestrator role + instance profile ---

resource "aws_iam_role" "ec2_orchestrator" {
  name = "${var.project_name}-ec2-orchestrator"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_instance_profile" "ec2_orchestrator" {
  name = "${var.project_name}-ec2-orchestrator"
  role = aws_iam_role.ec2_orchestrator.name
}

resource "aws_iam_role_policy" "ec2_orchestrator" {
  name = "${var.project_name}-ec2-orchestrator"
  role = aws_iam_role.ec2_orchestrator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ECRAuth"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Sid    = "ECRPullPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
        ]
        Resource = local.ecr_repo_arn_pattern
      },
      {
        Sid      = "S3List"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = local.artifact_bucket_arns_list
      },
      {
        Sid    = "S3ReadWrite"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = local.artifact_bucket_arns_objects
      },
      {
        Sid    = "BatchSubmit"
        Effect = "Allow"
        Action = [
          "batch:SubmitJob",
          "batch:TerminateJob",
        ]
        Resource = "*"
      },
      {
        Sid    = "BatchRead"
        Effect = "Allow"
        Action = [
          "batch:DescribeJobs",
          "batch:ListJobs",
        ]
        Resource = "*"
      },
      {
        Sid    = "PassRoleToBatch"
        Effect = "Allow"
        Action = "iam:PassRole"
        Resource = [
          aws_iam_role.batch_job.arn,
          aws_iam_role.batch_execution.arn,
        ]
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "ecs-tasks.amazonaws.com"
          }
        }
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams",
        ]
        Resource = local.ec2_log_group_arn
      },
      {
        Sid      = "SecretsManagerDBCreds"
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = local.rds_secret_arn_pattern
      },
    ]
  })
}

# --- Batch job role (application permissions for containers) ---

resource "aws_iam_role" "batch_job" {
  name = "${var.project_name}-batch-job"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "batch_job" {
  name = "${var.project_name}-batch-job"
  role = aws_iam_role.batch_job.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "S3List"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = local.artifact_bucket_arns_list
      },
      {
        Sid    = "S3ReadWrite"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = local.artifact_bucket_arns_objects
      },
    ]
  })
}

# --- Batch execution role (ECR pull + log shipping) ---

resource "aws_iam_role" "batch_execution" {
  name = "${var.project_name}-batch-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "batch_execution" {
  name = "${var.project_name}-batch-execution"
  role = aws_iam_role.batch_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ECRAuth"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Sid    = "ECRPull"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
        ]
        Resource = local.ecr_repo_arn_pattern
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = local.batch_log_group_arn
      },
    ]
  })
}

# --- Batch container instance role + instance profile ---

resource "aws_iam_role" "batch_instance" {
  name = "${var.project_name}-batch-instance"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_instance_profile" "batch_instance" {
  name = "${var.project_name}-batch-instance"
  role = aws_iam_role.batch_instance.name
}

resource "aws_iam_role_policy_attachment" "batch_instance_ecs" {
  role       = aws_iam_role.batch_instance.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

# --- Spot Fleet role ---

resource "aws_iam_role" "spot_fleet" {
  name = "${var.project_name}-spot-fleet"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "spotfleet.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "spot_fleet" {
  role       = aws_iam_role.spot_fleet.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2SpotFleetTaggingRole"
}

# --- Lambda execution role ---

resource "aws_iam_role" "lambda_execution" {
  name = "${var.project_name}-lambda-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_vpc_access" {
  role       = aws_iam_role.lambda_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "lambda_execution" {
  name = "${var.project_name}-lambda-execution"
  role = aws_iam_role.lambda_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "S3List"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = local.artifact_bucket_arns_list
      },
      {
        Sid      = "S3Read"
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = local.artifact_bucket_arns_objects
      },
      {
        Sid      = "SecretsManagerDBCreds"
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = local.rds_secret_arn_pattern
      },
      {
        Sid    = "BatchRead"
        Effect = "Allow"
        Action = "batch:DescribeJobs"
        Resource = "*"
      },
    ]
  })
}

# --- Batch service-linked role ---

resource "aws_iam_service_linked_role" "batch" {
  aws_service_name = "batch.amazonaws.com"
}
