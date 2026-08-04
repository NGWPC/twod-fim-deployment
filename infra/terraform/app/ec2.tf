locals {
  ec2_user_data = <<-SCRIPT
    #!/bin/bash
    set -euo pipefail
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
      docker.io docker-compose-v2 unzip curl ca-certificates
    systemctl enable --now docker
    usermod -aG docker ubuntu

    curl "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o /tmp/awscliv2.zip
    unzip -q /tmp/awscliv2.zip -d /tmp
    /tmp/aws/install
    rm -rf /tmp/awscliv2.zip /tmp/aws
    SCRIPT
}

resource "aws_key_pair" "deployer" {
  count = var.ec2_ssh_public_key != "" ? 1 : 0

  key_name   = "${var.project_name}-deployer"
  public_key = var.ec2_ssh_public_key

  tags = { Name = "${var.project_name}-deployer-key" }
}

resource "aws_instance" "orchestrator" {
  ami                         = var.ec2_ami_id
  instance_type               = var.ec2_instance_type
  subnet_id                   = var.private_subnet_ids[0]
  vpc_security_group_ids      = [aws_security_group.ec2.id]
  iam_instance_profile        = local.ec2_instance_profile_name
  key_name                    = var.ec2_ssh_public_key != "" ? aws_key_pair.deployer[0].key_name : null
  associate_public_ip_address = false

  root_block_device {
    volume_size = var.ec2_root_volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_tokens = "required"
  }

  user_data_replace_on_change = true

  user_data = local.ec2_user_data

  tags = { Name = "${var.project_name}-orchestrator" }
}

resource "aws_instance" "worker" {
  count = var.worker_count

  ami                         = var.ec2_ami_id
  instance_type               = var.ec2_instance_type
  subnet_id                   = var.private_subnet_ids[count.index % length(var.private_subnet_ids)]
  vpc_security_group_ids      = [aws_security_group.ec2.id]
  iam_instance_profile        = local.ec2_instance_profile_name
  key_name                    = var.ec2_ssh_public_key != "" ? aws_key_pair.deployer[0].key_name : null
  associate_public_ip_address = false

  root_block_device {
    volume_size = var.ec2_root_volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_tokens = "required"
  }

  user_data_replace_on_change = true

  user_data = local.ec2_user_data

  tags = { Name = "${var.project_name}-worker-${count.index}" }
}
