resource "aws_ecr_repository" "orchestrator" {
  name                 = "${var.project_name}-orchestrator"
  image_tag_mutability = var.ecr_image_tag_mutability
  force_delete         = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "${var.project_name}-ecr-orchestrator" }
}

resource "aws_ecr_lifecycle_policy" "orchestrator" {
  repository = aws_ecr_repository.orchestrator.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 30 untagged images"
      selection = {
        tagStatus   = "untagged"
        countType   = "imageCountMoreThan"
        countNumber = 30
      }
      action = {
        type = "expire"
      }
    }]
  })
}

resource "aws_ecr_repository" "model_worker" {
  name                 = "${var.project_name}-model-worker"
  image_tag_mutability = var.ecr_image_tag_mutability
  force_delete         = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "${var.project_name}-ecr-model-worker" }
}

resource "aws_ecr_lifecycle_policy" "model_worker" {
  repository = aws_ecr_repository.model_worker.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 30 untagged images"
      selection = {
        tagStatus   = "untagged"
        countType   = "imageCountMoreThan"
        countNumber = 30
      }
      action = {
        type = "expire"
      }
    }]
  })
}

resource "aws_ecr_repository" "nd_scenario_worker" {
  name                 = "${var.project_name}-nd-scenario-worker"
  image_tag_mutability = var.ecr_image_tag_mutability
  force_delete         = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "${var.project_name}-ecr-nd-scenario-worker" }
}

resource "aws_ecr_lifecycle_policy" "nd_scenario_worker" {
  repository = aws_ecr_repository.nd_scenario_worker.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 30 untagged images"
      selection = {
        tagStatus   = "untagged"
        countType   = "imageCountMoreThan"
        countNumber = 30
      }
      action = {
        type = "expire"
      }
    }]
  })
}

resource "aws_ecr_repository" "kwse_scenario_worker" {
  name                 = "${var.project_name}-kwse-scenario-worker"
  image_tag_mutability = var.ecr_image_tag_mutability
  force_delete         = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "${var.project_name}-ecr-kwse-scenario-worker" }
}

resource "aws_ecr_lifecycle_policy" "kwse_scenario_worker" {
  repository = aws_ecr_repository.kwse_scenario_worker.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 30 untagged images"
      selection = {
        tagStatus   = "untagged"
        countType   = "imageCountMoreThan"
        countNumber = 30
      }
      action = {
        type = "expire"
      }
    }]
  })
}
