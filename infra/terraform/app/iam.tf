# IAM roles for the app stack. The resources these roles grant access to (Batch job
# queue/definitions, ECR repos, CloudWatch log groups) are created in this same stack,
# so Batch actions can be scoped to actual resource ARNs instead of wildcards.
#
# Gated by var.create_iam:
#   true  -> create every role/instance profile below
#   false -> create nothing; consumers resolve to the existing_* variables via the
#            "resolved values for consumers" locals block at the bottom of this file

variable "create_iam" {
  description = "Create IAM roles for the app stack. Set false to reference existing roles via existing_* variables."
  type        = bool
  default     = true
}

variable "create_batch_service_linked_role" {
  description = "Create the AWSServiceRoleForBatch service-linked role. Set false if the account already has it."
  type        = bool
  default     = true
}

variable "existing_ec2_instance_profile_name" {
  description = "Existing EC2 orchestrator instance profile name (required when create_iam = false)"
  type        = string
  default     = ""

  validation {
    condition     = var.create_iam || var.existing_ec2_instance_profile_name != ""
    error_message = "existing_ec2_instance_profile_name is required when create_iam = false."
  }
}

variable "existing_batch_job_role_arn" {
  description = "Existing Batch job role ARN (required when create_iam = false)"
  type        = string
  default     = ""

  validation {
    condition     = var.create_iam || can(regex("^arn:aws[a-zA-Z-]*:iam::[0-9]{12}:role/", var.existing_batch_job_role_arn))
    error_message = "existing_batch_job_role_arn is required (and must be a role ARN) when create_iam = false."
  }
}

variable "existing_batch_execution_role_arn" {
  description = "Existing Batch execution role ARN (required when create_iam = false)"
  type        = string
  default     = ""

  validation {
    condition     = var.create_iam || can(regex("^arn:aws[a-zA-Z-]*:iam::[0-9]{12}:role/", var.existing_batch_execution_role_arn))
    error_message = "existing_batch_execution_role_arn is required (and must be a role ARN) when create_iam = false."
  }
}

variable "existing_batch_instance_profile_arn" {
  description = "Existing Batch instance profile ARN (required when create_iam = false)"
  type        = string
  default     = ""

  validation {
    condition     = var.create_iam || can(regex("^arn:aws[a-zA-Z-]*:iam::[0-9]{12}:instance-profile/", var.existing_batch_instance_profile_arn))
    error_message = "existing_batch_instance_profile_arn is required (and must be an instance-profile ARN) when create_iam = false."
  }
}

variable "existing_spot_fleet_role_arn" {
  description = "Existing Spot Fleet role ARN (required when create_iam = false)"
  type        = string
  default     = ""

  validation {
    condition     = var.create_iam || can(regex("^arn:aws[a-zA-Z-]*:iam::[0-9]{12}:role/", var.existing_spot_fleet_role_arn))
    error_message = "existing_spot_fleet_role_arn is required (and must be a role ARN) when create_iam = false."
  }
}

variable "existing_lambda_execution_role_arn" {
  description = "Existing Lambda execution role ARN (required when create_iam = false)"
  type        = string
  default     = ""

  validation {
    condition     = var.create_iam || can(regex("^arn:aws[a-zA-Z-]*:iam::[0-9]{12}:role/", var.existing_lambda_execution_role_arn))
    error_message = "existing_lambda_execution_role_arn is required (and must be a role ARN) when create_iam = false."
  }
}

variable "existing_batch_service_role_arn" {
  description = "Existing Batch service-linked role ARN (required when create_iam = false)"
  type        = string
  default     = ""

  validation {
    condition     = var.create_iam || can(regex("^arn:aws[a-zA-Z-]*:iam::[0-9]{12}:role/", var.existing_batch_service_role_arn))
    error_message = "existing_batch_service_role_arn is required (and must be a role ARN) when create_iam = false."
  }
}

variable "ssm_logging_policy_arn" {
  description = "Required SSM/logging policy ARN to attach to EC2 instance profiles (from account infra guide)"
  type        = string
  default     = ""

  validation {
    condition     = var.ssm_logging_policy_arn == "" || can(regex("^arn:aws[a-zA-Z-]*:iam::[0-9]{12}:policy/", var.ssm_logging_policy_arn))
    error_message = "ssm_logging_policy_arn must be empty or a valid IAM policy ARN."
  }
}

# --- Derived naming and partition-safe ARNs (GovCloud-safe) ---

locals {
  partition  = data.aws_partition.current.partition
  account_id = data.aws_caller_identity.current.account_id

  prod_bucket_arn = "arn:${local.partition}:s3:::${var.prod_bucket_name}"
  test_bucket_arn = "arn:${local.partition}:s3:::${var.test_bucket_name}"

  dagster_bucket_arn = "arn:${local.partition}:s3:::${var.dagster_s3_bucket}"

  artifact_bucket_arns_list = [
    local.prod_bucket_arn,
    local.test_bucket_arn,
    local.dagster_bucket_arn,
  ]

  artifact_bucket_arns_objects = [
    "${local.prod_bucket_arn}/*",
    "${local.test_bucket_arn}/*",
    "${local.dagster_bucket_arn}/*",
  ]

  # Read on buckets this account does not own. Requester-pays needs no extra
  # action - the charge follows the caller's credentials, not a permission - so
  # GetObject is the whole grant, and the caller sends x-amz-request-payer via
  # AWS_REQUEST_PAYER. Empty list yields no statements rather than an empty one,
  # which IAM rejects.
  external_source_statements = length(var.external_source_bucket_names) == 0 ? [] : [
    {
      Sid      = "S3ExternalSourceList"
      Effect   = "Allow"
      Action   = "s3:ListBucket"
      Resource = [for b in var.external_source_bucket_names : "arn:${local.partition}:s3:::${b}"]
    },
    {
      Sid      = "S3ExternalSourceRead"
      Effect   = "Allow"
      Action   = "s3:GetObject"
      Resource = [for b in var.external_source_bucket_names : "arn:${local.partition}:s3:::${b}/*"]
    },
  ]

  ec2_log_group_arn           = "arn:${local.partition}:logs:${var.region}:${local.account_id}:log-group:/aws/ec2/${var.project_name}:*"
  batch_log_group_arn         = "arn:${local.partition}:logs:${var.region}:${local.account_id}:log-group:/aws/batch/${var.project_name}:*"
  batch_default_log_group_arn = "arn:${local.partition}:logs:${var.region}:${local.account_id}:log-group:/aws/batch/job:*"

  rds_secret_name = "${var.project_name}/rds-credentials"

  # No resource-level ARN scoping exists for batch:SubmitJob/TerminateJob's job-instance
  # target (jobs aren't known until submission), so this scopes to the account/region at
  # least, rather than a bare "*".
  batch_job_arn_pattern     = "arn:${local.partition}:batch:${var.region}:${local.account_id}:job/*"
  batch_job_def_arn_pattern = "arn:${local.partition}:batch:${var.region}:${local.account_id}:job-definition/${var.project_name}-*"
}

# --- EC2 orchestrator role + instance profile ---
# Drives the Dagster orchestrator: pushes/pulls images, submits Batch jobs, reads DB creds.

resource "aws_iam_role" "ec2_orchestrator" {
  count = var.create_iam ? 1 : 0
  name  = "${var.project_name}-ec2-orchestrator"

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
  count = var.create_iam ? 1 : 0
  name  = "${var.project_name}-ec2-orchestrator"
  role  = aws_iam_role.ec2_orchestrator[0].name
}

resource "aws_iam_role_policy" "ec2_orchestrator" {
  count = var.create_iam ? 1 : 0
  name  = "${var.project_name}-ec2-orchestrator"
  role  = aws_iam_role.ec2_orchestrator[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
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
        Action = "batch:SubmitJob"
        Resource = [
          aws_batch_job_queue.scenarios.arn,
          local.batch_job_def_arn_pattern,
          local.batch_job_arn_pattern,
        ]
      },
      {
        Sid      = "BatchTerminate"
        Effect   = "Allow"
        Action   = "batch:TerminateJob"
        Resource = local.batch_job_arn_pattern
      },
      {
        Sid    = "BatchRead"
        Effect = "Allow"
        Action = [
          "batch:DescribeJobs",
          "batch:DescribeJobDefinitions",
          "batch:DescribeJobQueues",
          "batch:DescribeComputeEnvironments",
          "batch:ListJobs",
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = local.ec2_log_group_arn
      },
      {
        Sid      = "CloudWatchLogsDescribe"
        Effect   = "Allow"
        Action   = "logs:DescribeLogStreams"
        Resource = local.ec2_log_group_arn
      },
      {
        Sid      = "BatchLogsRead"
        Effect   = "Allow"
        Action   = "logs:GetLogEvents"
        Resource = local.batch_default_log_group_arn
      },
      {
        Sid      = "SecretsManagerDBCreds"
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = aws_secretsmanager_secret.rds_credentials.arn
      },
      {
        # manage_master_user_password (rds.tf) puts the actual master password in a
        # separate, AWS-managed secret rather than aws_secretsmanager_secret.rds_credentials
        # above (which holds connection metadata only). The orchestrator needs both.
        Sid      = "SecretsManagerRDSManagedPassword"
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = aws_db_instance.main.master_user_secret[0].secret_arn
      },
    ], local.external_source_statements)
  })
}

resource "aws_iam_role_policy" "ec2_orchestrator_ecr" {
  count = var.create_iam && var.create_ecr ? 1 : 0
  name  = "${var.project_name}-ec2-orchestrator-ecr"
  role  = aws_iam_role.ec2_orchestrator[0].id

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
        Resource = [
          aws_ecr_repository.orchestrator[0].arn,
          aws_ecr_repository.model_worker[0].arn,
          aws_ecr_repository.nd_scenario_worker[0].arn,
          aws_ecr_repository.kwse_scenario_worker[0].arn,
        ]
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ec2_orchestrator_ssm" {
  count      = var.create_iam && var.ssm_logging_policy_arn != "" ? 1 : 0
  role       = aws_iam_role.ec2_orchestrator[0].name
  policy_arn = var.ssm_logging_policy_arn
}

# --- Batch job role (application permissions for containers, jobRoleArn) ---

resource "aws_iam_role" "batch_job" {
  count = var.create_iam ? 1 : 0
  name  = "${var.project_name}-batch-job"

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
  count = var.create_iam ? 1 : 0
  name  = "${var.project_name}-batch-job"
  role  = aws_iam_role.batch_job[0].id

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

# --- Batch execution role (ECR pull + log shipping, executionRoleArn) ---

resource "aws_iam_role" "batch_execution" {
  count = var.create_iam ? 1 : 0
  name  = "${var.project_name}-batch-execution"

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
  count = var.create_iam ? 1 : 0
  name  = "${var.project_name}-batch-execution"
  role  = aws_iam_role.batch_execution[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
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

resource "aws_iam_role_policy" "batch_execution_ecr" {
  count = var.create_iam && var.create_ecr ? 1 : 0
  name  = "${var.project_name}-batch-execution-ecr"
  role  = aws_iam_role.batch_execution[0].id

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
        Resource = [
          aws_ecr_repository.nd_scenario_worker[0].arn,
          aws_ecr_repository.kwse_scenario_worker[0].arn,
        ]
      },
    ]
  })
}

# --- Batch container instance role + instance profile ---
# The ECS agent on each EC2 instance in the Batch compute environment: registers with ECS,
# pulls from ECR, ships awslogs. AWS-managed policy only.

resource "aws_iam_role" "batch_instance" {
  count = var.create_iam ? 1 : 0
  name  = "${var.project_name}-batch-instance"

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
  count = var.create_iam ? 1 : 0
  name  = "${var.project_name}-batch-instance"
  role  = aws_iam_role.batch_instance[0].name
}

resource "aws_iam_role_policy_attachment" "batch_instance_ecs" {
  count      = var.create_iam ? 1 : 0
  role       = aws_iam_role.batch_instance[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

# --- Spot Fleet role ---

resource "aws_iam_role" "spot_fleet" {
  count = var.create_iam ? 1 : 0
  name  = "${var.project_name}-spot-fleet"

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
  count      = var.create_iam ? 1 : 0
  role       = aws_iam_role.spot_fleet[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonEC2SpotFleetTaggingRole"
}

# --- Lambda execution role ---

resource "aws_iam_role" "lambda_execution" {
  count = var.create_iam ? 1 : 0
  name  = "${var.project_name}-lambda-execution"

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
  count      = var.create_iam ? 1 : 0
  role       = aws_iam_role.lambda_execution[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "lambda_execution" {
  count = var.create_iam ? 1 : 0
  name  = "${var.project_name}-lambda-execution"
  role  = aws_iam_role.lambda_execution[0].id

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
        Resource = aws_secretsmanager_secret.rds_credentials.arn
      },
      {
        Sid      = "BatchRead"
        Effect   = "Allow"
        Action   = "batch:DescribeJobs"
        Resource = "*"
      },
    ]
  })
}

# --- Batch service-linked role ---
# Account-global; AWS may have auto-created it already on first Batch use. Toggle off to
# skip creation and reference the account's existing one instead.

resource "aws_iam_service_linked_role" "batch" {
  count            = var.create_iam && var.create_batch_service_linked_role ? 1 : 0
  aws_service_name = "batch.amazonaws.com"
}

# --- Resolved values for consumers (batch.tf, ec2.tf, lambda.tf) ---
# Single source of truth: the resource created above when create_iam = true, the matching
# existing_* input otherwise. Consumers should reference these locals, never the resources
# or existing_* variables directly.

locals {
  ec2_instance_profile_name  = var.create_iam ? aws_iam_instance_profile.ec2_orchestrator[0].name : var.existing_ec2_instance_profile_name
  batch_job_role_arn         = var.create_iam ? aws_iam_role.batch_job[0].arn : var.existing_batch_job_role_arn
  batch_execution_role_arn   = var.create_iam ? aws_iam_role.batch_execution[0].arn : var.existing_batch_execution_role_arn
  batch_instance_profile_arn = var.create_iam ? aws_iam_instance_profile.batch_instance[0].arn : var.existing_batch_instance_profile_arn
  spot_fleet_role_arn        = var.create_iam ? aws_iam_role.spot_fleet[0].arn : var.existing_spot_fleet_role_arn
  lambda_execution_role_arn  = var.create_iam ? aws_iam_role.lambda_execution[0].arn : var.existing_lambda_execution_role_arn

  # 3-way toggle: create_iam = false takes the existing_* input; create_iam = true with
  # create_batch_service_linked_role = false falls back to the well-known ARN of the
  # AWS-managed role (service-linked roles cannot be created twice in one account); both
  # true resolves to the resource this file creates.
  batch_service_role_arn = var.create_iam ? (
    var.create_batch_service_linked_role
    ? aws_iam_service_linked_role.batch[0].arn
    : "arn:${local.partition}:iam::${local.account_id}:role/aws-service-role/batch.amazonaws.com/AWSServiceRoleForBatch"
  ) : var.existing_batch_service_role_arn
}
