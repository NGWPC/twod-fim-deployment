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
├── compatibility_policy   	# placeholder for future document
├── docker-compose.yml     	# for local deployment
├── example.env
├── orchestrator/           #  the self contained dagster project
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── recon/             	# tooling agnostic pkg for reconciliation loop
│   ├── dagster_app/       	# dagster specific code
│   ├── notebooks/
│   ├── scripts/
│   ├── testdata/
│   └── tests/
├── db/                     # schema, triggers, seeds, migrate.sh
├── infra/terraform/        # modules + envs/dev
└── scripts/ 								# could also call tools
```

## Justfile

Need just binary