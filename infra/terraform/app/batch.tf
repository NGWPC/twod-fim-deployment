locals {
  orchestrator_image_repo = var.create_ecr ? aws_ecr_repository.orchestrator[0].repository_url : var.orchestrator_image_repo
  build_model_image_repo  = var.create_ecr ? aws_ecr_repository.model_worker[0].repository_url : var.build_model_image_repo
  nd_image_repo           = var.create_ecr ? aws_ecr_repository.nd_scenario_worker[0].repository_url : var.nd_image_repo
  kwse_image_repo         = var.create_ecr ? aws_ecr_repository.kwse_scenario_worker[0].repository_url : var.kwse_image_repo
}

resource "aws_batch_compute_environment" "gpu" {
  name         = "${var.project_name}-gpu-${var.use_spot ? "spot" : "ec2"}"
  type         = "MANAGED"
  state        = "ENABLED"
  service_role = local.batch_service_role_arn

  compute_resources {
    type                = var.use_spot ? "SPOT" : "EC2"
    allocation_strategy = var.use_spot ? "SPOT_CAPACITY_OPTIMIZED" : "BEST_FIT_PROGRESSIVE"
    min_vcpus           = 0
    desired_vcpus       = 0
    max_vcpus           = var.batch_max_vcpus
    instance_type       = var.batch_instance_types
    subnets             = var.private_subnet_ids
    security_group_ids  = [aws_security_group.batch.id]
    instance_role       = local.batch_instance_profile_arn
    spot_iam_fleet_role = var.use_spot ? local.spot_fleet_role_arn : null

    tags = merge({
      ManagedBy = "Terraform"
      Project   = var.project_name
      Stack     = "app"
    }, local.optional_tags)
  }

  lifecycle {
    create_before_destroy = true
    ignore_changes        = [compute_resources[0].desired_vcpus]
  }

  depends_on = [
    aws_iam_role_policy_attachment.batch_instance_ecs,
    aws_iam_role_policy_attachment.spot_fleet,
  ]

  tags = { Name = "${var.project_name}-gpu-spot" }
}

resource "aws_batch_job_queue" "scenarios" {
  name     = "${var.project_name}-scenarios-queue"
  state    = "ENABLED"
  priority = 1

  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.gpu.arn
  }

  tags = { Name = "${var.project_name}-scenarios-queue" }
}

resource "aws_batch_job_definition" "nd" {
  name                  = "${var.project_name}-nd-scenarios"
  type                  = "container"
  platform_capabilities = ["EC2"]
  propagate_tags        = true

  retry_strategy {
    attempts = var.batch_retry_attempts

    evaluate_on_exit {
      action           = "RETRY"
      on_status_reason = "Host EC2*"
    }

    evaluate_on_exit {
      action    = "EXIT"
      on_reason = "*"
    }
  }

  timeout {
    attempt_duration_seconds = var.nd_job_timeout_seconds
  }

  container_properties = jsonencode({
    image            = "${local.nd_image_repo}:${var.nd_image_tag}"
    jobRoleArn       = local.batch_job_role_arn
    executionRoleArn = local.batch_execution_role_arn

    resourceRequirements = [
      { type = "VCPU", value = tostring(var.nd_job_vcpus) },
      { type = "MEMORY", value = tostring(var.nd_job_memory) },
      { type = "GPU", value = "1" },
    ]

    linuxParameters = {
      sharedMemorySize = var.batch_shared_memory_size
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.batch.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "nd"
      }
    }

    environment = [
      { name = "STORE_ROOT", value = "s3://${var.prod_bucket_name}" },
      { name = "TEST_STORE_ROOT", value = "s3://${var.test_bucket_name}" },
    ]
  })

  tags = { Name = "${var.project_name}-nd-scenarios" }
}

resource "aws_batch_job_definition" "kwse" {
  name                  = "${var.project_name}-kwse-scenarios"
  type                  = "container"
  platform_capabilities = ["EC2"]
  propagate_tags        = true

  retry_strategy {
    attempts = var.batch_retry_attempts

    evaluate_on_exit {
      action           = "RETRY"
      on_status_reason = "Host EC2*"
    }

    evaluate_on_exit {
      action    = "EXIT"
      on_reason = "*"
    }
  }

  timeout {
    attempt_duration_seconds = var.kwse_job_timeout_seconds
  }

  container_properties = jsonencode({
    image            = "${local.kwse_image_repo}:${var.kwse_image_tag}"
    jobRoleArn       = local.batch_job_role_arn
    executionRoleArn = local.batch_execution_role_arn

    resourceRequirements = [
      { type = "VCPU", value = tostring(var.kwse_job_vcpus) },
      { type = "MEMORY", value = tostring(var.kwse_job_memory) },
      { type = "GPU", value = "1" },
    ]

    linuxParameters = {
      sharedMemorySize = var.batch_shared_memory_size
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.batch.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "kwse"
      }
    }

    environment = [
      { name = "STORE_ROOT", value = "s3://${var.prod_bucket_name}" },
      { name = "TEST_STORE_ROOT", value = "s3://${var.test_bucket_name}" },
    ]
  })

  tags = { Name = "${var.project_name}-kwse-scenarios" }
}
