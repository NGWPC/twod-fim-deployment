# twod-fim-deployment

> [!WARNING]
> Draft Software. Many individual functions and code pieces are AI generated.

Deployment + orchestration for the 2D-FIM system. This repo is the **orchestrator**
and the **platform** it runs on. It does *not* contain the modeling jobs
(`build_model`, `run_nd_scenarios`, `run_kwse_scenarios` those live in
[`twod-fim-jobs`](https://github.com/NGWPC/twod-fim-jobs)) or the methodology
([`twod-fim-knowledge-base`](https://github.com/NGWPC/twod-fim-knowledge-base)).

Design references: `twod-fim-knowledge-base/system-design/` (`guide.md`, `orchestrator-design.md`, `triggers-and-propagation.md`)

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
├── orchestrator/            # reconciliation loop and job execution
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