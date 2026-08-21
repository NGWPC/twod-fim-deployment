# Cloud Deployment

Scripts for deploying the Dagster orchestrator and pipeline databases on EC2.

## Prerequisites

- AWS infrastructure up (`terraform apply` on the app stack)
- SSM access to the EC2 instance
- Repo cloned on EC2 to a persistent location
- `.env` created from `example.cloud.env` with:
  - RDS address (`POSTGRES_HOST`, `DAGSTER_PG_HOST` from `terraform output -raw rds_address`)
  - Application user passwords (`DAGSTER_PG_PASSWORD`, `POSTGRES_PASSWORD`)
  - RDS master secret ARN (`RDS_SECRET_ARN` from `terraform output -raw rds_master_user_secret_arn`)
  - S3 bucket names (`DAGSTER_S3_BUCKET`, `ARTIFACTS_S3_BUCKET`)
  - Container images (`ORCHESTRATOR_IMAGE`, `BUILD_MODEL_IMAGE`)

## Scripts

| Script | Purpose |
|---|---|
| `setup.py` | One-command setup: installs psql, fetches master password, initializes databases, deploys services |
| `init_db.py` | Database only: creates users, databases, schema, permissions |
| `deploy.py` | Services only: pulls images, starts three Dagster services (code-server, webserver, daemon) |
| `setup_sepex.py` | SEPEX deployment: database, clone, configure, build, start (see [sepex.md](sepex.md)) |

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

# Run everything (images default to .env values, or override via CLI):
python3 deploy/setup.py \
  --orchestrator-image ghcr.io/ngwpc/twod-fim-deployment/orchestrator:<tag> \
  --build-model-image ghcr.io/ngwpc/twod-fim-jobs/build_model:<tag>
```

### Update to latest code

```bash
cd /opt/twod-fim-deployment
git pull
python3 deploy/setup.py --skip-db
```

### Clean slate (reset databases + redeploy)

```bash
python3 deploy/setup.py --reset
```

### Redeploy services only (no database changes)

```bash
python3 deploy/setup.py --skip-db
```

### Update to a specific image version

```bash
# Option 1: edit .env and redeploy
# Edit ORCHESTRATOR_IMAGE or BUILD_MODEL_IMAGE in .env, then:
python3 deploy/setup.py --skip-db

# Option 2: override via CLI args (does not change .env)
python3 deploy/setup.py --skip-db \
  --orchestrator-image ghcr.io/ngwpc/twod-fim-deployment/orchestrator:sha-abc1234 \
  --build-model-image ghcr.io/ngwpc/twod-fim-jobs/build_model:sha-def5678
```

### Standalone usage

```bash
# Database init only (requires PGPASSWORD):
export PGPASSWORD=<master-password>
python3 deploy/init_db.py
python3 deploy/init_db.py --reset

# Service deploy only (reads images from .env):
python3 deploy/deploy.py
python3 deploy/deploy.py --orchestrator-image ghcr.io/.../orchestrator:sha-abc1234
```

## Verify

### Check services

```bash
docker compose -f docker-compose.cloud.yaml ps
```

All three services (code-server, webserver, daemon) should show `running`.

### Dagster UI

```bash
aws ssm start-session --target <instance-id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=3000,localPortNumber=3000 --profile sandbox
```

Open http://localhost:3000.

### Smoke check

For testing only. Seeds a small test network, triggers the reconciliation pipeline, and verifies DB state and S3 artifacts.

```bash
# On EC2, copy scripts and test data into the running container:
cd /opt/twod-fim-deployment
docker cp orchestrator/scripts twodfim-code-server:/app/scripts
docker cp orchestrator/testdata twodfim-code-server:/app/testdata
docker exec -it twodfim-code-server bash

# Inside the container:
pip install geopandas
cd /app

# Seed network table only (for SEPEX testing without Dagster):
python scripts/smoke_check.py --seed-only --network-only

# Seed network + desired_state (then watch in Dagster UI):
python scripts/smoke_check.py --seed-only

# Full check (seed + wait for reconciliation + verify):
python scripts/smoke_check.py
```

Before running the smoke check, enable the `reconciliation_sensor` in the Dagster UI:
Automation -> toggle `reconciliation_sensor` ON.

### Manual verification

```bash
# Check DB state:
psql -h <rds-address> -U twodfim_app -d twodfim -c \
  "SELECT reach_id, identity_hash, model_id, processing FROM twodfim.current_state;"

# Check S3 artifacts:
aws s3 ls s3://<artifacts-bucket>/v1/ --recursive
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `RDS_SECRET_ARN env var is required` | Add `RDS_SECRET_ARN` to `.env` (get from `terraform output -raw rds_master_user_secret_arn`) |
| `code-server not healthy` | Check `docker compose -f docker-compose.cloud.yaml logs code-server` |
| `password authentication failed` | Wrong password in `.env`, or user not created (run `setup.py` without `--skip-db`) |
| `database does not exist` | Run `setup.py` without `--skip-db` |
| `AccessDenied on LULC raster` | Set `DOCKER_DATA_DIR` and `LULC_SOURCE` in `.env` to use local raster |
