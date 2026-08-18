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

## 4. Create SEPEX database on RDS

From the SEPEX EC2 instance:

Wait for user-data to finish (step 3) before running apt-get.

```bash
sudo apt-get install -y postgresql-client

# Fetch master password
export PGPASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id <rds-master-user-secret-arn> \
  --query 'SecretString' --output text | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['password'])")

# Create database and user
psql -h <rds-address> -U twodfim_admin -d postgres <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sepex_app') THEN
    CREATE USER sepex_app WITH PASSWORD '<choose-a-password>';
  END IF;
END $$;
CREATE DATABASE sepex OWNER sepex_app;
SQL

# PG 16: transfer public schema ownership so sepex_app can create tables
psql -h <rds-address> -U twodfim_admin -d sepex -c \
  "ALTER SCHEMA public OWNER TO sepex_app;"
```

## 5. Clone and configure SEPEX

```bash
sudo git clone https://github.com/Dewberry/sepex.git /opt/sepex
sudo chown -R ssm-user:ssm-user /opt/sepex
cd /opt/sepex
```

Create a cloud compose file (api only, no minio/postgres since we use RDS and S3):

```bash
docker network create process_api_net 2>/dev/null || true

cat > docker-compose.cloud.yaml << 'EOF'
services:
  api:
    build:
      context: ./api
    container_name: sepex-api
    ports:
      - '80:5050'
    env_file:
      - .env
    volumes:
      - ./api/plugins:/app/plugins
      - ./.data/api:/.data
      - /var/run/docker.sock:/var/run/docker.sock
    networks:
      - process_api_net

networks:
  process_api_net:
    external: true
EOF
```

Create `.env` for cloud (not based on `example.env` - too many local-only vars):

```bash
cat > .env << 'EOF'
# --- Core
REPO_URL='https://github.com/Dewberry/sepex'
API_NAME='sepex'
API_PORT='5050'

# --- File & Logging
LOG_LEVEL='INFO'
LOG_FILE='/.data/logs/api.jsonl'
TMP_JOB_LOGS_DIR='/.data/tmp/job_logs'

# --- Database (RDS)
DB_SERVICE='postgres'
POSTGRES_CONN_STRING='postgres://sepex_app:<password>@<rds-address>:5432/sepex?sslmode=require'

# --- Policies
EXPIRY_DAYS='7'

# --- Storage (S3, credentials via instance profile)
STORAGE_SERVICE='aws-s3'
STORAGE_BUCKET='<artifacts-bucket-name>'
STORAGE_METADATA_PREFIX='metadata'
STORAGE_RESULTS_PREFIX='results'
STORAGE_LOGS_PREFIX='logs'

# --- AWS
AWS_REGION='us-east-1'
BATCH_LOG_STREAM_GROUP='/aws/batch/job'

# --- Auth (disabled for testing)
AUTH_SERVICE=''
AUTH_LEVEL='0'

# --- Plugins
PLUGINS_LOAD_DIR=''
PLUGINS_DIR='/.data/plugins'

# --- Queue Resource Limits
MAX_LOCAL_CPUS=''
MAX_LOCAL_MEMORY_MB=''
EOF
```

Fill in `<password>`, `<rds-address>`, and `<artifacts-bucket-name>` with actual values.
If the password contains special characters (`!`, `@`, `[`, `)`, etc.), URL-encode it:

```bash
python3 -c "from urllib.parse import quote; print(quote('<password>', safe=''))"
```

## 6. Build and start SEPEX

Build SEPEX image from source on EC2:

```bash
cd /opt/sepex
docker compose -f docker-compose.cloud.yaml build
docker compose -f docker-compose.cloud.yaml up -d
```

## 7. Verify

```bash
# Check service
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
