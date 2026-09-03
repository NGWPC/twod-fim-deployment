# twod-fim-deployment

Deployment + orchestration for the 2D-FIM system. This repo is the **orchestrator**
and the **platform** it runs on. It does *not* contain the modeling jobs
(`build_model`, `run_nd_scenarios`, `run_kwse_scenarios` those live in
[`twod-fim-jobs`](https://github.com/NGWPC/twod-fim-jobs)) or the methodology
([`twod-fim-knowledge-base`](https://github.com/NGWPC/twod-fim-knowledge-base)).

Design references: `twod-fim-knowledge-base/system-design/` (`guide.md`, `orchestrator-design.md`, `triggers-and-propagation.md`)

## Layout

```
├── README.md
├── justfile
├── compatibility_policy   	 # placeholder for future document
├── docker-compose-local.yml # local infrastructure (db, minio, sepex)
├── example.env
├── example.cloud.env
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