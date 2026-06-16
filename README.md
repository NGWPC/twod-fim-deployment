# twod-fim-deployment

Deployment + orchestration for the 2D-FIM system. This repo is the **orchestrator**
and the **platform** it runs on. It does *not* contain the modeling jobs
(`build_model`, `run_nd_scenarios`, `run_kwse_scenarios` those live in
[`twod-fim-jobs`](../twod-fim-jobs)) or the methodology
([`twod-fim-knowledge-base`](../twod-fim-knowledge-base)).

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
│   ├── src/orchestrator/
│   └── tests/
├── db/                     # schema, triggers, seeds, migrate.sh
├── infra/terraform/        # modules + envs/dev
└── scripts/ 								# could also call tools
```