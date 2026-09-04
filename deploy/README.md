# Cloud Deployment

Scripts for initializing the pipeline database and deploying SEPEX on EC2.

## Prerequisites

- AWS infrastructure up (`terraform apply` on the app stack)
- SSM access to the EC2 instance
- Repo cloned on EC2 to a persistent location
- `.env` created from `example.cloud.env` with:
  - RDS address (`POSTGRES_HOST` from `terraform output -raw rds_address`)
  - Application user password (`POSTGRES_PASSWORD`)
  - RDS master secret ARN (`RDS_SECRET_ARN` from `terraform output -raw rds_master_user_secret_arn`)
  - S3 bucket name (`ARTIFACTS_S3_BUCKET`)
  - SEPEX URL (`SEPEX_URL`)

## Scripts

| Script | Purpose |
|---|---|
| `init_db.py` | Database setup: fetches RDS master password from SecretsManager, creates user, database, schema, permissions (idempotent) |
| `setup_sepex.py` | SEPEX deployment: database, configure, image pull, start (see [sepex.md](sepex.md)) |

## Common workflows

### Connect to EC2

```bash
aws ssm start-session --target <instance-id> --profile sandbox

# First time: add ssm-user to docker group
sudo usermod -aG docker ssm-user
# Reconnect for it to take effect
exit
aws ssm start-session --target <instance-id> --profile sandbox
```

### First deploy

```bash
# On EC2:
sudo git clone https://github.com/NGWPC/twod-fim-deployment.git /opt/twod-fim-deployment
sudo chown -R ssm-user:ssm-user /opt/twod-fim-deployment
cd /opt/twod-fim-deployment

# One-time: create .env
cp example.cloud.env .env
# Edit .env with real values

# Install psycopg (required by init_db.py)
cd orchestrator && uv pip install -e .
cd /opt/twod-fim-deployment

# Initialize the database
python3 deploy/init_db.py

# Deploy SEPEX (see deploy/sepex.md for full guide)
python3 deploy/setup_sepex.py \
  --rds-address <rds-address> \
  --rds-secret-arn <rds-secret-arn> \
  --sepex-password <password> \
  --s3-bucket <bucket-name>

# Seed the network, then author intent for it
cd orchestrator
uv run python scripts/seed.py
uv run python scripts/author_intent.py --scope all

# Start the reconciler
uv run python scripts/reconcile.py --forever
```

### Update to latest code

```bash
cd /opt/twod-fim-deployment
git pull
```

### Clean slate (reset database)

```bash
python3 deploy/init_db.py --reset
```

## Verify

### Check reconciler

```bash
cd /opt/twod-fim-deployment/orchestrator
uv run python scripts/reconcile.py --once
```

### Smoke check

Seeds a small test network and runs the reconciliation loop.

```bash
cd /opt/twod-fim-deployment/orchestrator

# Seed the network, then author the small end-to-end scope
uv run python scripts/seed.py
uv run python scripts/author_intent.py

# Run one reconciliation pass
uv run python scripts/reconcile.py --once

# Run until settled
uv run python scripts/reconcile.py --forever
```

### Manual verification

psql is not required by the deploy scripts but is useful for ad-hoc inspection.

```bash
# Check DB state (requires psql installed):
psql -h <rds-address> -U twodfim_app -d twodfim -c \
  "SELECT state, count(*) FROM reach_status GROUP BY state ORDER BY state;"

# Check S3 artifacts:
aws s3 ls s3://<artifacts-bucket>/version=v1/ --recursive
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `RDS_SECRET_ARN env var is required` | Add `RDS_SECRET_ARN` to `.env` (get from `terraform output -raw rds_master_user_secret_arn`) |
| `password authentication failed` | Wrong password in `.env`, or user not created (run `init_db.py`) |
| `database does not exist` | Run `init_db.py` to initialize |
| SEPEX unreachable | Check SEPEX is running: `curl http://<sepex-ip>/` (see [sepex.md](sepex.md)) |
