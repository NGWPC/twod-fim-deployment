# twod-fim-deployment

> [!NOTE]
>
> This repo is draft software developed for OWP by the NGWPC team led by Entarian. The repo is not production ready. Further experiments, testing, quality control, and careful consideration are required before adopting this software. Many individual code pieces and functions are *AI generated* and not carefully reviewed.

Deployment + reconciliation for the 2D-FIM system. This repo is the **reconciler**
and the **platform** it runs on. It does *not* contain the modeling jobs
(`build_model`, `run_nd_scenarios`, `run_kwse_scenarios` those live in
[`twod-fim-jobs`](https://github.com/NGWPC/twod-fim-jobs)) or the methodology
([`twod-fim-knowledge-base`](https://github.com/NGWPC/twod-fim-knowledge-base)).

Design references: `twod-fim-knowledge-base/system-design/` (`guide.md`, `reconciler-design.md`, `triggers-and-propagation.md`)

## Getting Started

You need [Docker](https://docs.docker.com/get-docker/), [uv](https://docs.astral.sh/uv/)
and [just](https://github.com/casey/just). Everything else runs in docker stack that the following commands will bring up.

Start by copying the example environment file:

```bash
cp example.env .env
```

The defaults work as-is for a local run. The one setting that need to be decided up front is
`GPU_AVAILABLE`: leave it unset on an ordinary machine, or set it to `true` if the
host has an NVIDIA GPU and the NVIDIA Container Toolkit, in which case the loop asks
for the GPU variant of each job and the stack gives SEPEX the host's GPUs.

Then bring the stack up:

```bash
just up-local
```

That creates the shared docker network and starts PostGIS on `5432`, applying
`db/schema/*.sql` on first boot; MinIO on `9000`, with its console on `9001`; and
SEPEX on `5050`. It then registers the process definitions under `sepex/local/plugins`
with SEPEX and writes `desired_state_defaults` from your `.env`. SEPEX pulls the
published job images as it registers each process, so the first run is slow.

At this point the platform is up but has nothing to do: the database holds defaults
and no reaches. Getting a network in and saying what you want of it is the subject of
[RUNBOOK.md](RUNBOOK.md), which walks one area of interest from source data through
to published depth grids.

Once a network is seeded and its intent authored, the loop does the work:

```bash
just reconcile
```

Each pass checks the reaches that need looking at, submits jobs for whatever is
missing, and stops once the network settles. To keep it running instead, invoke the
script directly with `--forever`:

```bash
cd reconciler && uv run python scripts/reconcile.py --forever
```

To start from scratch, `just wipe` deletes the database, bucket and SEPEX state
(it asks first), and `just up-local` rebuilds them.

Running `just` on its own lists every recipe. [reconciler/README.md](reconciler/README.md)
covers the environment variables and local development in more detail.

## Layout

```
├── README.md
├── RUNBOOK.md               # producing libraries for an AOI
├── justfile
├── compatibility_policy   	 # placeholder for future document
├── docker-compose.yml       # profiles: local (db, minio) + local-cpu or local-gpu (sepex), hybrid (db)
├── example.env
├── example.cloud.env
├── example.aoi_config.jsonc # every AOI config option, commented (see RUNBOOK.md)
├── reconciler/            # reconciliation loop and job execution
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── recon/               # reconciliation loop package
│   ├── scripts/             # reconcile.py, seed.py, bound_flows.py
│   ├── notebooks/
│   ├── testdata/
│   └── tests/
├── deploy/                  # init_db, setup, SEPEX plugin configs
├── sepex/                   # SEPEX local plugin configuration
├── db/                      # schema SQL (includes triggers)
└── infra/terraform/         # modules + envs/dev
```

## Justfile

Need just binary