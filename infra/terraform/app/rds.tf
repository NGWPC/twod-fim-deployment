resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db-subnet-group"
  subnet_ids = var.private_subnet_ids

  tags = { Name = "${var.project_name}-db-subnet-group" }
}

resource "aws_db_instance" "main" {
  identifier = "${var.project_name}-postgres"

  engine         = "postgres"
  engine_version = var.rds_engine_version
  instance_class = var.rds_instance_class

  allocated_storage = var.rds_allocated_storage
  storage_encrypted = true

  db_name                     = "dagster"
  username                    = var.rds_master_username
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  multi_az               = var.rds_multi_az

  backup_retention_period   = var.rds_backup_retention_days
  skip_final_snapshot       = var.rds_skip_final_snapshot
  final_snapshot_identifier = var.rds_skip_final_snapshot ? null : var.rds_final_snapshot_identifier
  deletion_protection       = var.rds_deletion_protection
  apply_immediately         = true

  tags = { Name = "${var.project_name}-postgres" }

  lifecycle {
    precondition {
      condition     = var.rds_skip_final_snapshot || (var.rds_final_snapshot_identifier != null && var.rds_final_snapshot_identifier != "")
      error_message = "rds_final_snapshot_identifier is required when rds_skip_final_snapshot is false."
    }
  }
}

resource "aws_secretsmanager_secret" "rds_credentials" {
  name                    = local.rds_secret_name
  recovery_window_in_days = var.rds_secret_recovery_window_days

  tags = { Name = "${var.project_name}-rds-credentials" }
}

resource "aws_secretsmanager_secret_version" "rds_credentials" {
  secret_id = aws_secretsmanager_secret.rds_credentials.id

  # Connection metadata only. The master password lives in the AWS-managed secret created
  # by manage_master_user_password above (aws_db_instance.main.master_user_secret[0].secret_arn),
  # so no plaintext credential ever lands here or in Terraform state.
  secret_string = jsonencode({
    host       = aws_db_instance.main.address
    port       = aws_db_instance.main.port
    username   = aws_db_instance.main.username
    dagster_db = "dagster"
  })
}
