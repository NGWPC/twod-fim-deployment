resource "aws_ecr_repository" "orchestrator" {
  name                 = "${var.ecr_repository_name_prefix}orchestrator"
  image_tag_mutability = var.ecr_image_tag_mutability
  force_delete         = var.ecr_force_delete

  tags = { Name = "${var.project_name}-ecr-orchestrator" }
}

resource "aws_ecr_repository" "model_worker" {
  name                 = "${var.ecr_repository_name_prefix}model-worker"
  image_tag_mutability = var.ecr_image_tag_mutability
  force_delete         = var.ecr_force_delete

  tags = { Name = "${var.project_name}-ecr-model-worker" }
}

resource "aws_ecr_repository" "nd_scenario_worker" {
  name                 = "${var.ecr_repository_name_prefix}nd-scenario-worker"
  image_tag_mutability = var.ecr_image_tag_mutability
  force_delete         = var.ecr_force_delete

  tags = { Name = "${var.project_name}-ecr-nd-scenario-worker" }
}

resource "aws_ecr_repository" "kwse_scenario_worker" {
  name                 = "${var.ecr_repository_name_prefix}kwse-scenario-worker"
  image_tag_mutability = var.ecr_image_tag_mutability
  force_delete         = var.ecr_force_delete

  tags = { Name = "${var.project_name}-ecr-kwse-scenario-worker" }
}
