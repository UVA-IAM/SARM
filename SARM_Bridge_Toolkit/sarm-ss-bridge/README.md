# SARM Source System Bridge

A SARM Source System endpoint — it connects a datasource (SQL database and/or LDAP directory) to the SARM protocol over HTTP. The bridge answers **"what do you have that needs attesting?"** via Scope Discovery and accepts attestation decisions back via Decision Notification.

## Quick start

### Docker Swarm (production / remote host)

The bridge ships with a seeded SQLite sample dataset so you can demonstrate the full round-trip with **zero external dependencies**.

```bash
# From the toolkit root:

# 1. Build the image
./build.sh                          # docker build -t sarm-ss-bridge ./sarm-ss-bridge

# 2. Deploy to Swarm
docker stack deploy -c docker-compose.yml sarm

# 3. Verify
curl http://localhost:8080/health
# → {"status":"ok"}

# 4. Remove and redeploy (e.g. after code changes)
./redeploy.sh

# 5. Remove and reseed (full clean slate)
./redeploy_reseed.sh
```

| Script | What it does |
|---|---|
| `./build.sh` | Builds `sarm-ss-bridge:latest` |
| `./build_deploy.sh` | Build + deploy in one step |
| `./redeploy.sh` | Tear down, rebuild, redeploy |
| `./redeploy_reseed.sh` | Tear down, **delete data volume**, rebuild, redeploy |

Clean up the stack entirely:

```bash
docker stack rm sarm
docker config rm sarm_bridge_config   # clean up the config reference
# Optional: docker volume rm sarm_bridge-data   # wipes seed data
```

### Docker Compose (local development)

`docker stack deploy` does not support `build:`, so local iteration uses plain Compose:

```bash
docker compose up --build
# Bridge at http://localhost:8000
```

### Native Python (local development)

```bash
cd sarm-ss-bridge

# Seed the sample SQLite database
python -m app.seed.seed

# Start the dev server (hot-reload enabled)
uvicorn app.main:app --reload

# Verify
curl localhost:8000/health
```

## What it does

The bridge maps rows or directory entries from your datasource onto **SARM ScopeItems** — an identity (certifier) answering a question about one or more objects. It exposes two protocol surfaces:

| Surface | Method | Path | Purpose |
|---|---|---|---|
| **Scope Discovery** | `GET` | `/sarm/v1/ScopeItems` | Returns objects in scope, who must attest, and what decisions are allowed |
| **Scope Item** | `GET` | `/sarm/v1/ScopeItems/{id}` | Fetch a single scope item by ID |
| **Decision Notification** | `POST` | `/sarm/v1/Decisions` | Accepts a certifier's decision; applies configured actions |
| **Capability Exchange** | `POST` | `/sarm/v1/capabilities` | Advertises conformance level and supported features |
| Health | `GET` | `/health` | Liveness probe (200 = alive) |

> Full endpoint shapes (query params, request/response payloads, status codes) are defined by the SARM spec in `sarm-spec/`.

## Configuration

The bridge is configured through three layers, in order of precedence (highest first):

1. **Environment variables** — secrets and runtime toggles (`.env` or Docker `environment:`).
2. **Docker Config / YAML file** — datasource URLs, scope queries, action mappings (`config.yaml`).
3. **Defaults** — baked into the code; overridden by anything above.

### Key environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SARM_DATABASE_URL` | `sqlite:////app/data/sample.sqlite` | SQLAlchemy connection string |
| `SARM_CONFIG` | `/app/config.yaml` | Path to the YAML config file |
| `SARM_DECISIONS_SYNC_MODE` | `sync` | `sync` (returns immediately) or `async` (returns 202, awaits completion) |
| `SARM_DECISIONS_REPLAY_DISPOSITION` | `allow` | `allow` (accept replayed decisions) or `disallow` (409 Conflict) |
| `SARM_DECISIONS_DRY_RUN` | `true` | Log decisions without applying them |
| `SARM_BEARER_TOKEN` | — | Bearer token for local testing only |
| `LOG_LEVEL` | `INFO` | Python log level |
| `UVICORN_HOST` | `0.0.0.0` | Bind address |
| `UVICORN_PORT` | `8000` | Bind port |

### YAML config (`config.example.yaml`)

See `config.example.yaml` for the full reference. Key sections:

- **`database.url`** — SQLAlchemy connection string. The sample uses SQLite; for production, use `postgresql+psycopg2://`, `mysql+pymysql://`, `oracle+cx_oracle://`, or `mssql+pyodbc://`.
- **`conformance_level`** — advertised conformance (1 = basic pagination, 2 = filtering + attribute selection, 3 = push events).
- **`dry_run_decisions`** — when `true`, decision actions are logged but never executed.
- **`scope_queries`** — SQL queries that map database rows to SARM ScopeItems, with column mappings for subject, certifier, resource, and context data.
- **`decision_actions`** — maps decision values (e.g. `remove_membership`) to actions (`none`, `write`, `dry-run`) and optional SQL statements.

### Docker Swarm config injection

In Swarm mode, `config.example.yaml` is loaded as a **Docker Config** (`sarm_bridge_config`) and mounted into the container at `/app/config.yaml`. To update config without rebuilding the image:

```bash
docker config create sarm_bridge_config ./sarm-ss-bridge/config.yaml
docker service update --config-rm sarm_bridge_config --config-add source=sarm_bridge_config,target=/app/config.yaml sarm_bridge
docker service scale sarm_bridge=0 && docker service scale sarm_bridge=1   # rolling restart
```

## Database & seeding

The Docker image **auto-seeds** a SQLite database at build time (`app/seed/seed.py`) into `/app/data/sample.sqlite`, persisted via the `bridge-data` named volume. The sample dataset contains:

- A handful of certifiers and scope items
- Decision options per item (e.g. `remove_membership`, `keep_membership`)
- Context data fields (`memberSince`, `lastAccessAt`, `addedBy`)

To start fresh (wipe and reseed):

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
│   ├── discovery.py         # Scope Discovery surface (GET /ScopeItems)
│   ├── decisions.py         # Decision Notification surface (POST /Decisions)
│   ├── capabilities.py      # Capability Exchange (POST /capabilities)
│   ├── return_channel.py    # Async completion event handling
│   ├── sources/
│   │   └── sql_source.py    # SQLAlchemy Core query runner
│   └── seed/
│       └── seed.py          # Sample SQLite dataset
├── config.example.yaml      # Full config reference
├── Dockerfile               # Multi-stage: pip install → COPY app → seed → run
└── pyproject.toml           # Dependencies: fastapi, uvicorn, sqlalchemy, pydantic, pyyaml, ldap3
```

## Security considerations

- **Parameterized queries only** — all SQL uses bound parameters, never string interpolation.
- **Dry-run by default** — decision actions must be explicitly enabled in config.
- **Write actions are gated** — the `decision_actions` mapping must explicitly define a `write` action with a SQL statement; misconfiguration won't silently mutate data.
- **Bearer tokens** — SARM is auth-agnostic. This bridge does not enforce authentication; tokens are for testing only.

## Connecting a real database

1. Update `database.url` in your config (or `SARM_DATABASE_URL` env var) to point at your database.
2. Adjust the `scope_queries` SQL and column mappings to match your schema.
3. Define `decision_actions` to map SARM decision values to your database operations.
4. Set `dry_run_decisions: false` when ready to execute actions for real.
5. Rebuild and redeploy: `./redeploy.sh`
