# SARM Interop Toolkit

Developer/QA toolkit for proving [SARM](sarm-spec/SARM_Specification_Draft_v0.3.txt) (System for Attestation and Recertification Management) interoperability end to end.

SARM is a draft interoperability specification for systems that perform attestation and recertification. This toolkit **proves it works** by wiring two independently built programs together:

1. **[sarm-ss-bridge](sarm-ss-bridge/)** — A Source System bridge harness. Connects to a SQL database and/or LDAP directory, exposes data as a SARM Source System over HTTP.

2. **[sarm-inspector](sarm-inspector/)** — A single-page web tool. Point it at a SARM endpoint, discover scope items, send decisions, and watch the raw HTTP traffic.

Both programs conform to the spec in [sarm-spec/](sarm-spec/) but share **no runtime code**. They talk over HTTP — like two strangers who both read the spec.

## TL;DR — one command, full round-trip

```bash
./build_deploy.sh
# Bridge up at http://localhost:8080
# Open sarm-inspector/index.html in a browser, enter http://localhost:8080 as the endpoint
```

That's it. You now have a SARM Source System with sample data and a protocol inspector pointed at it.

---

## Quick start

### Docker Swarm (recommended — production or remote host)

The bridge ships with a seeded SQLite sample dataset so you can demonstrate the full round-trip with **zero external dependencies**.

```bash
# 1. Build + deploy (one command)
./build_deploy.sh

# 2. Verify the bridge is alive
curl http://localhost:8080/health
# → {"status":"ok"}

# 3. Open the inspector
#    Open sarm-inspector/index.html in a browser
#    Endpoint: http://localhost:8080
#    Token: (leave blank — sample data has no auth)

# 4. In the inspector: discover → pick a scope item → send a decision
```

**Redeploy after code changes:**
```bash
./redeploy.sh          # tear down, rebuild, redeploy
./redeploy_reseed.sh   # same, plus wipe the data volume (fresh seed)
```

**Clean up:**
```bash
docker stack rm sarm
docker config rm sarm_bridge_config
# docker volume rm sarm_bridge-data   # optional: wipe seed data
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
python -m app.seed.seed
uvicorn app.main:app --reload
```

See **[sarm-ss-bridge/README.md](sarm-ss-bridge/)** for bridge-specific configuration, env vars, and connecting a real database.

---

## The two programs

### sarm-ss-bridge — Source System endpoint

A SARM Source System that connects a datasource to the protocol. It maps rows from a SQL database (or LDAP directory) onto **SARM ScopeItems** — an identity (certifier) answering a question about one or more objects.

**Protocol surfaces:**

| Surface | Method | Path | Purpose |
|---|---|---|---|
| Scope Discovery | `GET` | `/sarm/v1/ScopeItems` | Objects in scope, who must attest, allowed decisions |
| Scope Item | `GET` | `/sarm/v1/ScopeItems/{id}` | Single scope item by ID |
| Decision Notification | `POST` | `/sarm/v1/Decisions` | Accept a certifier's decision |
| Capability Exchange | `POST` | `/sarm/v1/capabilities` | Conformance level and features |
| Health | `GET` | `/health` | Liveness probe |

See **[sarm-ss-bridge/README.md](sarm-ss-bridge/)** for full configuration details, deployment scripts, and connecting to production databases.

### sarm-inspector — Protocol inspector

A single, self-contained HTML file. No build step, no CDN, no dependencies. Open it in a browser and point it at any SARM endpoint.

**What it does:**

1. **Configure** — enter the endpoint URL and an optional bearer token.
2. **Discover** — fetches scope items from the Source System and renders them as a table.
3. **Inspect** — click a row to see details: subject, certifier, decision options, context data.
4. **Decide** — select an item, choose from the **SS-declared** decision options, toggle dry-run/send, and submit.
5. **Watch** — every HTTP request and response is logged in collapsible accordions (method, URL, headers, pretty-printed JSON body, status code).

**How to use:**

```
1. Open sarm-inspector/index.html in a browser
2. Enter the bridge endpoint:  http://localhost:8080
3. (Optional) Enter a bearer token
4. Click "Discover" — scope items appear in the table
5. Click a row to inspect it
6. Choose a decision option, toggle dry-run or send
7. Watch the raw HTTP traffic below
```

The inspector shows a visible notice: *"Bearer token only — this is a protocol demonstrator, not an auth reference. SARM is auth-agnostic."*

---

## Repository structure

```
sarm-spec/              — The SARM spec (prose draft + machine-readable schema)
sarm-ss-bridge/         — Source System bridge (FastAPI + SQLAlchemy)
sarm-inspector/         — Protocol inspector (single HTML file)
docs/                   — Schema findings, interop run reports
build.sh                — Build the bridge Docker image
build_deploy.sh         — Build + deploy to Docker Swarm
redeploy.sh             — Tear down, rebuild, redeploy
redeploy_reseed.sh      — Tear down, delete data, rebuild, redeploy
docker-compose.yml      — Swarm stack (bridge + config + volume)
```

---

## How it works — the round-trip

```
┌──────────────┐     Scope Discovery      ┌──────────────┐
│              │  GET /sarm/v1/ScopeItems  │              │
│  Inspector   │ ──────────────────────►  │    Bridge      │
│              │  (scope items)            │  (SQLite/LDAP) │
└──────┬───────┘                          └──────────────┘
       │
       │ Pick an item, choose a decision
       │
       ▼  Decision Notification
┌──────┴───────┐                          ┌──────────────┐
│              │  POST /sarm/v1/Decisions  │              │
│  Inspector   │ ──────────────────────►  │    Bridge      │
│              │  (decision + action)      │  (log/apply)   │
└──────────────┘                          └──────────────┘
```

The bridge turns datasource rows into SARM ScopeItems. The inspector discovers them, lets you act on one, and sends the decision back. **Both sides are independently built** — they agree only because they both conform to the spec.

---

## Phases

This toolkit is built in phases. Current phase: **Phase 2 — Bridge skeleton** (bridge skeleton with health, config, SQLite seed).

See [CLAUDE.md](CLAUDE.md) for the full phased plan.

---

## Security notes

- All SQL queries use parameterized values — never string-interpolated input.
- Decision actions default to DRY-RUN — actual writes require explicit config.
- Bearer tokens are for testing only — SARM is auth-agnostic (§8 of the spec).
- Never commit real tokens or secrets.

---

## Schema findings

As we build, we find places where the SARM spec is silent, ambiguous, or awkward. These are a primary deliverable, not an annoyance.

See [docs/schema-findings.md](docs/schema-findings.md) for the running list.
