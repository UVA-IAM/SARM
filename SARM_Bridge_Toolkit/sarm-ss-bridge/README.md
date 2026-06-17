# SARM Source System Bridge

A SARM Source System endpoint — it connects a datasource (SQL database and/or LDAP directory) to the SARM protocol over HTTP. The bridge answers **"what do you have that needs attesting?"** via Scope Discovery and accepts attestation decisions back via Decision Notification.

**Quick start** (full round-trip with inspector): see the [top-level README](../README.md).

## Deployment

### Docker Swarm (production / remote host)

From the toolkit root:

```bash
./build_deploy.sh          # build + deploy
curl http://localhost:8080/health
```

| Script | What it does |
|---|---|
| `./build.sh` | Builds `sarm-ss-bridge:latest` |
| `./build_deploy.sh` | Build + deploy in one step |
| `./redeploy.sh` | Tear down, rebuild, redeploy |
| `./redeploy_reseed.sh` | Tear down, **delete data volume**, rebuild, redeploy |

Clean up:
```bash
docker stack rm sarm
docker config rm sarm_bridge_config
```

### Docker Compose (local dev)

`docker stack deploy` does not support `build:` — use plain Compose for local iteration:

```bash
docker compose up --build
# Bridge at http://localhost:8000
```

### Native Python (local dev)

```bash
cd sarm-ss-bridge
python -m app.seed.seed
uvicorn app.main:app --reload
```

## Protocol surfaces

| Surface | Method | Path | Purpose |
|---|---|---|---|
| Scope Discovery | `GET` | `/sarm/v1/ScopeItems` | Objects in scope, who must attest, allowed decisions |
| Scope Item | `GET` | `/sarm/v1/ScopeItems/{id}` | Single scope item by ID |
| Decision Notification | `POST` | `/sarm/v1/Decisions` | Accept a certifier's decision |
| Capability Exchange | `POST` | `/sarm/v1/capabilities` | Conformance level and features |
| Health | `GET` | `/health` | Liveness probe |

Full endpoint shapes (query params, request/response payloads, status codes) are defined by the SARM spec in [sarm-spec/](../sarm-spec/).

## Configuration

Three layers, highest precedence first:

1. **Environment variables** — secrets and runtime toggles
2. **YAML config** — datasource URLs, scope queries, action mappings
3. **Code defaults** — overridden by anything above

### Key environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SARM_DATABASE_URL` | `sqlite:////app/data/sample.sqlite` | SQLAlchemy connection string |
| `SARM_CONFIG` | `/app/config.yaml` | Path to YAML config file |
| `SARM_DECISIONS_SYNC_MODE` | `sync` | `sync` (returns immediately) or `async` (returns 202) |
| `SARM_DECISIONS_REPLAY_DISPOSITION` | `allow` | `allow` or `disallow` (409 Conflict) |
| `SARM_DECISIONS_DRY_RUN` | `true` | Log without applying actions |
| `SARM_BEARER_TOKEN` | — | Bearer token for local testing |
| `LOG_LEVEL` | `INFO` | Python log level |
| `UVICORN_HOST` | `0.0.0.0` | Bind address |
| `UVICORN_PORT` | `8000` | Bind port |

### YAML config (`config.example.yaml`)

See `config.example.yaml` for the full reference. Key sections:

- **`database.url`** — SQLAlchemy connection string. SQLite by default; for production use `postgresql+psycopg2://`, `mysql+pymysql://`, `oracle+cx_oracle://`, or `mssql+pyodbc://`.
- **`conformance_level`** — advertised conformance (1 = basic pagination, 2 = filtering + attribute selection, 3 = push events).
- **`dry_run_decisions`** — when `true`, decision actions are logged but never executed.
- **`scope_queries`** — SQL queries mapping database rows to SARM ScopeItems, with column mappings for subject, certifier, resource, and context data.
- **`decision_actions`** — maps decision values (e.g. `remove_membership`) to actions (`none`, `write`, `dry-run`) and optional SQL statements.

### Docker Swarm config injection

In Swarm mode, `config.example.yaml` is loaded as a **Docker Config** (`sarm_bridge_config`) and mounted into the container at `/app/config.yaml`. To update config without rebuilding:

```bash
docker config create sarm_bridge_config ./sarm-ss-bridge/config.yaml
docker service update --config-rm sarm_bridge_config --config-add source=sarm_bridge_config,target=/app/config.yaml sarm_bridge
docker service scale sarm_bridge=0 && docker service scale sarm_bridge=1
```

## Database & seeding

The Docker image **auto-seeds** a SQLite database at build time into `/app/data/sample.sqlite`, persisted via the `bridge-data` named volume. The sample dataset contains:

- A handful of certifiers and scope items
- Decision options per item (`remove_membership`, `keep_membership`)
- Context data fields (`memberSince`, `lastAccessAt`, `addedBy`)

To start fresh:

```bash
# Swarm
./redeploy_reseed.sh

# Native
rm -f app/data.db && python -m app.seed.seed
```

## Architecture

```
sarm-ss-bridge/
├── app/
│   ├── main.py              # FastAPI app, routes, middleware, lifespan
│   ├── config.py            # YAML + env var loading
│   ├── models.py            # Pydantic models aligned to sarm-spec/schema
│   ├── discovery.py         # Scope Discovery (GET /ScopeItems)
│   ├── decisions.py         # Decision Notification (POST /Decisions)
│   ├── capabilities.py      # Capability Exchange (POST /capabilities)
│   ├── return_channel.py    # Async completion event handling
│   ├── sources/
│   │   └── sql_source.py    # SQLAlchemy Core query runner
│   └── seed/
│       └── seed.py          # Sample SQLite dataset
├── config.example.yaml      # Full config reference
├── Dockerfile               # pip install → COPY app → seed → run
└── pyproject.toml           # fastapi, uvicorn, sqlalchemy, pydantic, pyyaml, ldap3
```

## Security

- **Parameterized queries only** — all SQL uses bound parameters.
- **Dry-run by default** — decision actions must be explicitly enabled in config.
- **Write actions are gated** — the `decision_actions` mapping must explicitly define a `write` action; misconfiguration won't silently mutate data.
- **Bearer tokens** — SARM is auth-agnostic. This bridge does not enforce authentication; tokens are for testing only.

## Connecting a real database

1. Update `database.url` in your config (or `SARM_DATABASE_URL`) to point at your database.
2. Adjust the `scope_queries` SQL and column mappings to match your schema.
3. Define `decision_actions` to map SARM decision values to your database operations.
4. Set `dry_run_decisions: false` when ready to execute actions for real.
5. Rebuild and redeploy: `./redeploy.sh`
