# All resources here are gated by var.create_storage:
#   true  -> create prod + test artifact S3 buckets
#   false -> create nothing; the app layer uses var.existing_prod_bucket_name / existing_test_bucket_name

locals {
  prod_bucket_name    = var.prod_bucket_name != "" ? var.prod_bucket_name : "${var.project_name}-prod-${data.aws_caller_identity.current.account_id}"
  test_bucket_name    = var.test_bucket_name != "" ? var.test_bucket_name : "${var.project_name}-test-${data.aws_caller_identity.current.account_id}"
  dagster_bucket_name = var.dagster_bucket_name != "" ? var.dagster_bucket_name : "${var.project_name}-dagster-${data.aws_caller_identity.current.account_id}"
}

# --- Prod artifact bucket ---

resource "aws_s3_bucket" "prod" {
  count = var.create_storage ? 1 : 0

  bucket = local.prod_bucket_name

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "prod" {
  count = var.create_storage ? 1 : 0

  bucket = aws_s3_bucket.prod[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "prod" {
  count = var.create_storage ? 1 : 0

  bucket = aws_s3_bucket.prod[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "prod" {
  count = var.create_storage ? 1 : 0

  bucket = aws_s3_bucket.prod[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "prod" {
  count = var.create_storage ? 1 : 0

  bucket = aws_s3_bucket.prod[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.prod[0].arn,
        "${aws_s3_bucket.prod[0].arn}/*",
      ]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.prod]
}

# --- Test artifact bucket ---

resource "aws_s3_bucket" "test" {
  count = var.create_storage ? 1 : 0

  bucket        = local.test_bucket_name
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "test" {
  count = var.create_storage ? 1 : 0

  bucket = aws_s3_bucket.test[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "test" {
  count = var.create_storage ? 1 : 0

  bucket = aws_s3_bucket.test[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "test" {
  count = var.create_storage ? 1 : 0

  bucket = aws_s3_bucket.test[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "test" {
  count = var.create_storage ? 1 : 0

  bucket = aws_s3_bucket.test[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.test[0].arn,
        "${aws_s3_bucket.test[0].arn}/*",
      ]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.test]
}

# --- Dagster compute logs bucket ---

resource "aws_s3_bucket" "dagster" {
  count = var.create_storage ? 1 : 0

  bucket        = local.dagster_bucket_name
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "dagster" {
  count = var.create_storage ? 1 : 0

  bucket = aws_s3_bucket.dagster[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dagster" {
  count = var.create_storage ? 1 : 0

  bucket = aws_s3_bucket.dagster[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "dagster" {
  count = var.create_storage ? 1 : 0

  bucket = aws_s3_bucket.dagster[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "dagster" {
  count = var.create_storage ? 1 : 0

  bucket = aws_s3_bucket.dagster[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.dagster[0].arn,
        "${aws_s3_bucket.dagster[0].arn}/*",
      ]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.dagster]
}
