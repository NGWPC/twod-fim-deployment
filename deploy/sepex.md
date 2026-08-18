# SEPEX Deployment

Manual deployment of SEPEX alongside the twod-fim orchestrator.
SEPEX is not managed by Terraform - it runs on a separate EC2 instance with its own security group.

## Prerequisites

- twod-fim app stack deployed (`terraform apply` on `infra/terraform/app/`)
- Orchestrator running on EC2 (`deploy/setup.py` completed)
- Values from the orchestrator instance:
  - VPC ID, subnet ID, instance profile ARN
  - Orchestrator security group ID
  - RDS security group ID, RDS address
- AMI ID from `ec2_ami_id` in `infra/terraform/app/terraform.tfvars`

Gather these from terraform output and the orchestrator instance:

```bash
# From infra/terraform/app/:
terraform output

# From the orchestrator instance:
aws ec2 describe-instances \
  --instance-ids <orchestrator-instance-id> \
  --query 'Reservations[0].Instances[0].{SG:SecurityGroups[0].GroupId,Subnet:SubnetId,AMI:ImageId,Profile:IamInstanceProfile.Arn,VPC:VpcId}' \
  --output table --profile sandbox

# RDS security group:
aws ec2 describe-security-groups \
  --filters 'Name=group-name,Values=twod-fim-rds*' \
  --query 'SecurityGroups[0].GroupId' --output text --profile sandbox
```

## 1. Create security group

SEPEX gets its own SG to avoid coupling with Terraform-managed resources.

```bash
aws ec2 create-security-group \
  --group-name sepex-ec2 \
  --description "SEPEX API server" \
  --vpc-id <vpc-id> \
  --profile sandbox
```

Note the returned `GroupId`.

```bash
# Allow orchestrator to reach SEPEX API (port 80)
aws ec2 authorize-security-group-ingress \
  --group-id <sepex-sg-id> \
  --protocol tcp --port 80 \
  --source-group <orchestrator-sg-id> \
  --profile sandbox

# Allow SEPEX to reach RDS (port 5432)
aws ec2 authorize-security-group-ingress \
  --group-id <rds-sg-id> \
  --protocol tcp --port 5432 \
  --source-group <sepex-sg-id> \
  --profile sandbox
```

Note: the RDS SG rule is added manually to a Terraform-managed SG.
Running `terraform apply` on the app stack will remove it - re-add after each apply.

## 2. Launch EC2 instance

Same AMI, instance type, subnet, and instance profile as the orchestrator.

```bash
aws ec2 run-instances \
  --image-id <ami-id> \
  --instance-type t3.xlarge \
  --subnet-id <subnet-id> \
  --security-group-ids <sepex-sg-id> \
  --iam-instance-profile Arn=<instance-profile-arn> \
  --metadata-options HttpTokens=required \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=30,VolumeType=gp3,Encrypted=true}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=sepex},{Key=Project,Value=twod-fim}]' \
  --user-data '#!/bin/bash
set -euo pipefail
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 unzip curl ca-certificates
systemctl enable --now docker
usermod -aG docker ubuntu
curl "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install
rm -rf /tmp/awscliv2.zip /tmp/aws' \
  --profile sandbox
```

Note the returned `InstanceId` or get it from console.

## 3. Connect and set up

Wait for user-data to finish (can take a few minutes on TGW networks):

```bash
aws ssm start-session --target <sepex-instance-id> --profile sandbox

# Watch cloud-init progress - wait for "Cloud-init finished"
sudo tail -f /var/log/cloud-init-output.log

# Then add ssm-user to docker group
sudo usermod -aG docker ssm-user
exit
aws ssm start-session --target <sepex-instance-id> --profile sandbox
```

## 4. Deploy SEPEX

From the SEPEX EC2 instance, clone the twod-fim-deployment repo and run the setup script.
The script handles: database creation, repo clone, configuration, build, and startup.

```bash
cd /home/ssm-user
sudo git clone -b feature/cloud-deploy https://github.com/NGWPC/twod-fim-deployment.git /opt/twod-fim-deployment
sudo chown -R ssm-user:ssm-user /opt/twod-fim-deployment

python3 /opt/twod-fim-deployment/deploy/setup_sepex.py \
  --rds-address <rds-address> \
  --rds-secret-arn <rds-master-user-secret-arn> \
  --sepex-password <choose-a-password> \
  --s3-bucket <artifacts-bucket-name>
```

| Argument | Source |
|---|---|
| `--rds-address` | `terraform output -raw rds_address` |
| `--rds-secret-arn` | `terraform output -raw rds_master_user_secret_arn` |
| `--sepex-password` | User-chosen password for the `sepex_app` database user |
| `--s3-bucket` | `prod_bucket_name` or `test_bucket_name` from `terraform.tfvars` |
| `--repo-url` | SEPEX repo (default: `https://github.com/Dewberry/sepex.git`) |
| `--install-dir` | Install path (default: `/opt/sepex`) |
| `--reset` | Drop and recreate the sepex database |
| `--skip-db` | Skip database setup, only redeploy |

The password is automatically URL-encoded in the connection string.

## 5. Verify

```bash
# Check service (from SEPEX instance)
cd /opt/sepex
docker compose -f docker-compose.cloud.yaml ps

# Test API (from SEPEX instance)
curl http://localhost/api/processes

# Test from orchestrator EC2 (via SEPEX private IP)
curl http://<sepex-private-ip>/api/processes
```

## Cleanup

To remove SEPEX without affecting the twod-fim infrastructure:

```bash
# Terminate the instance
aws ec2 terminate-instances --instance-ids <sepex-instance-id> --profile sandbox

# Delete the security group (after instance terminates)
aws ec2 delete-security-group --group-id <sepex-sg-id> --profile sandbox

# Remove the RDS SG ingress rule
aws ec2 revoke-security-group-ingress \
  --group-id <rds-sg-id> \
  --protocol tcp --port 5432 \
  --source-group <sepex-sg-id> \
  --profile sandbox

# Drop the database
psql -h <rds-address> -U twodfim_admin -d postgres -c "DROP DATABASE IF EXISTS sepex; DROP USER IF EXISTS sepex_app;"
```
