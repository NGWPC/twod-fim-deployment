# SEPEX Deployment

SEPEX is an OGC API server that manages container execution for compute jobs (build_model, nd_scenarios).
It runs on a separate EC2 instance outside Terraform management.

## Prerequisites

- twod-fim app stack deployed (`terraform apply` on `infra/terraform/app/`)
- Orchestrator running on EC2 (`deploy/setup.py` completed) (see [README.md](README.md))

### Variable reference

Placeholders used throughout this guide and where each value comes from:

| Placeholder | Source |
|---|---|
| `<vpc-id>` | `terraform output -raw vpc_id` |
| `<subnet-id>` | `terraform output -json private_subnet_ids` (pick one) |
| `<orchestrator-sg-id>` | `terraform output -raw orchestrator_security_group_id` |
| `<instance-profile-name>` | `terraform output -raw ec2_instance_profile_name` |
| `<ami-id>` | `terraform output -raw ec2_ami_id` |
| `<rds-sg-id>` | `terraform output -raw rds_security_group_id` |
| `<lambda-sg-id>` | `terraform output -raw lambda_security_group_id` |
| `<rds-address>` | `terraform output -raw rds_address` |
| `<rds-master-user-secret-arn>` | `terraform output -raw rds_master_user_secret_arn` |
| `<artifacts-bucket-name>` | `terraform output -raw prod_bucket_name` or `test_bucket_name` |
| `<sepex-sg-id>` | Returned by step 1 (`create-security-group`) |
| `<sepex-instance-id>` | Returned by step 2 (`run-instances`) |
| `<sepex-private-ip>` | `aws ec2 describe-instances --instance-ids <sepex-instance-id> --query 'Reservations[0].Instances[0].PrivateIpAddress' --output text --profile sandbox` |

### Gather values

All values come from terraform output (run from `infra/terraform/app/`):

```bash
terraform output
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

# Allow Lambda (Batch status callback) to reach SEPEX API (port 80)
aws ec2 authorize-security-group-ingress \
  --group-id <sepex-sg-id> \
  --protocol tcp --port 80 \
  --source-group <lambda-sg-id> \
  --profile sandbox

# Allow SEPEX to reach RDS (port 5432)
aws ec2 authorize-security-group-ingress \
  --group-id <rds-sg-id> \
  --protocol tcp --port 5432 \
  --source-group <sepex-sg-id> \
  --profile sandbox
```

Note: all three rules reference Terraform-managed SGs (orchestrator, Lambda, RDS).
Running `terraform apply` on the app stack may recreate those SGs with new IDs - re-add all three rules after each apply.

## 2. Launch EC2 instance

Same AMI, instance type, subnet, and instance profile as the orchestrator.

```bash
aws ec2 run-instances \
  --image-id <ami-id> \
  --instance-type t3.xlarge \
  --subnet-id <subnet-id> \
  --security-group-ids <sepex-sg-id> \
  --iam-instance-profile Name=<instance-profile-name> \
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
The script handles: database creation, image pull, configuration, and startup.

```bash
cd /home/ssm-user
sudo git clone https://github.com/NGWPC/twod-fim-deployment.git /opt/twod-fim-deployment
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
| `--image` | Container image (default: `ghcr.io/biplovbhandari/sepex:dev`) |
| `--install-dir` | Install path (default: `/opt/sepex`) |
| `--reset` | Drop and recreate the sepex database |
| `--skip-db` | Skip database setup, only deploy |

The password is automatically URL-encoded in the connection string.
Record the chosen password - it is needed for redeployments without `--reset`.

## 5. Register plugins

Copy the twod-fim plugin files from the deployment repo to the SEPEX plugins directory:

```bash
sudo mkdir -p /opt/sepex/.data/api/plugins/twod-fim
sudo cp /opt/twod-fim-deployment/deploy/plugins/*.yml /opt/sepex/.data/api/plugins/twod-fim/
```

Restart SEPEX to pick up the plugins:

```bash
cd /opt/sepex
docker compose -f docker-compose.cloud.yaml restart
```

## 6. Verify

```bash
# Check service (from SEPEX instance)
cd /opt/sepex
docker compose -f docker-compose.cloud.yaml ps

# Test API (from SEPEX instance)
curl http://localhost/

# Check plugins are registered
curl http://localhost/processes

# Test from orchestrator EC2 (via SEPEX private IP)
curl http://<sepex-private-ip>/
```

## 7. Connect Lambda to SEPEX

Update the Lambda's `PROCESS_API_URL` so Batch status callbacks reach SEPEX.
From `infra/terraform/app/`:

```bash
# Edit terraform.tfvars:
#   sepex_api_url = "http://<sepex-private-ip>"
terraform apply
```

Verify only the Lambda environment variable changes before applying.

## Cleanup

To remove SEPEX without affecting the twod-fim infrastructure:

```bash
# 1. Terminate the instance and wait
aws ec2 terminate-instances --instance-ids <sepex-instance-id> --profile sandbox
aws ec2 wait instance-terminated --instance-ids <sepex-instance-id> --profile sandbox

# 2. Remove the RDS SG ingress rule (must happen before SG delete -
#    the SEPEX SG can't be deleted while the RDS SG references it)
aws ec2 revoke-security-group-ingress \
  --group-id <rds-sg-id> \
  --protocol tcp --port 5432 \
  --source-group <sepex-sg-id> \
  --profile sandbox

# 3. Delete the SEPEX security group
aws ec2 delete-security-group --group-id <sepex-sg-id> --profile sandbox

# 4. Drop the database (run from orchestrator EC2 via SSM)
psql -h <rds-address> -U twodfim_admin -d postgres -c "DROP DATABASE IF EXISTS sepex; DROP USER IF EXISTS sepex_app;"
```

## Rebuilding after terraform destroy

SEPEX depends on Terraform-managed resources (RDS, security groups, Batch job definitions).
A `terraform destroy` + `apply` cycle recreates these with new IDs and addresses, which breaks SEPEX:

- RDS is destroyed - the `sepex` database and connection string are gone
- Orchestrator, Lambda, and RDS security groups get new IDs - SEPEX SG rules become stale
- The RDS SG also has a manually-added rule referencing the SEPEX SG (bidirectional dependency)
- Batch job definitions and queues are recreated - plugin references may break

**Destroy SEPEX first, then follow this guide from scratch after `terraform apply`:**

```bash
# 1. Terminate the SEPEX instance and wait for it to fully terminate
aws ec2 terminate-instances --instance-ids <sepex-instance-id> --profile sandbox
aws ec2 wait instance-terminated --instance-ids <sepex-instance-id> --profile sandbox

# 2. Revoke the RDS SG rule that references the SEPEX SG
#    (must happen before SG delete - AWS blocks deletion of a referenced SG)
aws ec2 revoke-security-group-ingress \
  --group-id <rds-sg-id> \
  --protocol tcp --port 5432 \
  --source-group <sepex-sg-id> \
  --profile sandbox

# 3. Delete the SEPEX security group
#    Look up the ID if needed:
#    aws ec2 describe-security-groups --filters "Name=group-name,Values=sepex-ec2" \
#      --query 'SecurityGroups[0].GroupId' --output text --profile sandbox
aws ec2 delete-security-group --group-id <sepex-sg-id> --profile sandbox

# 4. Terraform destroy
cd infra/terraform/app
terraform destroy

# 5. Rebuild
terraform apply

# 6. Deploy orchestrator (deploy/setup.py) and gather prereq values
#    (see Prerequisites section above)

# 7. Follow this guide from "Create security group" onward

# 8. After SEPEX EC2 is running, update the Lambda with the new SEPEX IP
#    Edit terraform.tfvars: sepex_api_url = "http://<sepex-private-ip>"
terraform apply
```
