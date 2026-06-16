resource "aws_key_pair" "deployer" {
  key_name   = "${var.project_name}-deployer"
  public_key = var.ec2_ssh_public_key

  tags = { Name = "${var.project_name}-deployer-key" }
}

resource "aws_instance" "orchestrator" {
  ami                    = data.aws_ssm_parameter.al2023_ami.value
  instance_type          = var.ec2_instance_type
  subnet_id              = var.public_subnet_ids[0]
  vpc_security_group_ids = [var.ec2_security_group_id]
  iam_instance_profile   = var.ec2_instance_profile_name
  key_name               = aws_key_pair.deployer.key_name

  associate_public_ip_address = true

  root_block_device {
    volume_size = var.ec2_root_volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = <<-EOF
    #!/bin/bash
    set -euo pipefail
    yum update -y
    yum install -y docker unzip curl
    systemctl enable docker
    systemctl start docker
    usermod -aG docker ec2-user

    mkdir -p /usr/local/lib/docker/cli-plugins
    curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
      -o /usr/local/lib/docker/cli-plugins/docker-compose
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

    curl "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o /tmp/awscliv2.zip
    unzip -q /tmp/awscliv2.zip -d /tmp
    /tmp/aws/install
    rm -rf /tmp/awscliv2.zip /tmp/aws
  EOF

  tags = { Name = "${var.project_name}-orchestrator" }
}

resource "aws_instance" "worker" {
  count = var.worker_count

  ami                    = data.aws_ssm_parameter.al2023_ami.value
  instance_type          = var.ec2_instance_type
  subnet_id              = var.public_subnet_ids[count.index % length(var.public_subnet_ids)]
  vpc_security_group_ids = [var.ec2_security_group_id]
  iam_instance_profile   = var.ec2_instance_profile_name
  key_name               = aws_key_pair.deployer.key_name

  associate_public_ip_address = true

  root_block_device {
    volume_size = var.ec2_root_volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = aws_instance.orchestrator.user_data

  tags = { Name = "${var.project_name}-worker-${count.index}" }
}
