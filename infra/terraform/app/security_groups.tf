# App-owned security groups: EC2 orchestrator, RDS, Batch compute, Lambda. The app stack
# fully owns every resource whose reachability rules depend on app-layer roles and workloads.
#
# The VPC endpoints security group itself lives in foundation (it protects
# infrastructure-level interface endpoints shared by anything in the VPC); this file only
# adds the ingress rules that let the SGs below reach it, per foundation/networking.tf's
# "ingress rules added by app stack" contract (see var.vpce_security_group_id).

variable "allowed_admin_cidrs" {
  description = "CIDR blocks allowed for SSH (:22) and Dagster UI (:3000) ingress. Empty for SSM-only access."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for c in var.allowed_admin_cidrs : can(cidrhost(c, 0))])
    error_message = "All allowed_admin_cidrs must be valid CIDR notation."
  }
}

variable "vpce_security_group_id" {
  description = "VPC interface endpoints security group ID. Optional - leave empty if no interface endpoints exist (e.g. TGW-based egress)."
  type        = string
  default     = ""

  validation {
    condition     = var.vpce_security_group_id == "" || can(regex("^sg-", var.vpce_security_group_id))
    error_message = "vpce_security_group_id must be empty or a security group ID starting with 'sg-'."
  }
}

# --- EC2 orchestrator ---

resource "aws_security_group" "ec2" {
  name_prefix = "${var.project_name}-ec2-"
  description = "EC2 orchestrator: SSH + Dagster UI from admin CIDRs, all egress"
  vpc_id      = var.vpc_id

  tags = { Name = "${var.project_name}-ec2-sg" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "ec2_ssh" {
  count = length(var.allowed_admin_cidrs)

  security_group_id = aws_security_group.ec2.id
  cidr_ipv4         = var.allowed_admin_cidrs[count.index]
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "ec2_dagster_ui" {
  count = length(var.allowed_admin_cidrs)

  security_group_id = aws_security_group.ec2.id
  cidr_ipv4         = var.allowed_admin_cidrs[count.index]
  from_port         = 3000
  to_port           = 3000
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "ec2_all" {
  security_group_id = aws_security_group.ec2.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- RDS ---
# No egress rules: Postgres never needs to initiate outbound connections.

resource "aws_security_group" "rds" {
  name_prefix = "${var.project_name}-rds-"
  description = "RDS Postgres: ingress from EC2 and Lambda SGs only"
  vpc_id      = var.vpc_id

  tags = { Name = "${var.project_name}-rds-sg" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_ec2" {
  security_group_id            = aws_security_group.rds.id
  referenced_security_group_id = aws_security_group.ec2.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_lambda" {
  security_group_id            = aws_security_group.rds.id
  referenced_security_group_id = aws_security_group.lambda.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

# --- Batch compute ---

resource "aws_security_group" "batch" {
  name_prefix = "${var.project_name}-batch-"
  description = "Batch compute instances: all egress for S3, ECR, CloudWatch"
  vpc_id      = var.vpc_id

  tags = { Name = "${var.project_name}-batch-sg" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_egress_rule" "batch_all" {
  security_group_id = aws_security_group.batch.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- Lambda ---

resource "aws_security_group" "lambda" {
  name_prefix = "${var.project_name}-lambda-"
  description = "Lambda: all egress for RDS, S3, Secrets Manager, Batch API"
  vpc_id      = var.vpc_id

  tags = { Name = "${var.project_name}-lambda-sg" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_egress_rule" "lambda_all" {
  security_group_id = aws_security_group.lambda.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- VPC endpoint ingress ---
# The SG is foundation's; only the ingress rules are ours. EC2 and Lambda both run on
# private subnets and need endpoint access (ECR/S3/Secrets Manager/Batch); Batch compute
# instances do too once they move off public subnets.

resource "aws_vpc_security_group_ingress_rule" "vpce_from_ec2" {
  count = var.vpce_security_group_id != "" ? 1 : 0

  security_group_id            = var.vpce_security_group_id
  referenced_security_group_id = aws_security_group.ec2.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "vpce_from_batch" {
  count = var.vpce_security_group_id != "" ? 1 : 0

  security_group_id            = var.vpce_security_group_id
  referenced_security_group_id = aws_security_group.batch.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "vpce_from_lambda" {
  count = var.vpce_security_group_id != "" ? 1 : 0

  security_group_id            = var.vpce_security_group_id
  referenced_security_group_id = aws_security_group.lambda.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
}
