# SARM Interop Toolkit

Developer/QA toolkit for proving [SARM](sarm-spec/SARM_Specification_Draft_v0.3.txt) (System for Attestation and Recertification Management) interoperability end to end.

## What this is

SARM is a draft interoperability specification for systems that perform attestation and recertification. This toolkit **proves it works** by wiring two independently built programs together:

1. **sarm-ss-bridge** — A Source System bridge harness. Connects to a SQL database and/or LDAP directory, exposes data as a SARM Source System over HTTP.

2. **sarm-inspector** — A single-page web tool. Point it at a SARM endpoint, discover scope items, send decisions, and watch the raw HTTP traffic.

Both programs conform to the spec in `sarm-spec/` but share **no runtime code**. They talk over HTTP — like two strangers who both read the spec.

## Quick start

```bash
# 1. Seed the sample database
cd sarm-ss-bridge
python -m app.seed.seed

# 2. Start the bridge
uvicorn app.main:app --reload

# 3. Verify
curl localhost:8000/health

# 4. Open sarm-inspector/index.html in a browser, point it at http://localhost:8000
```

Or with Docker (zero dependencies):

```bash
docker compose up
# Bridge is at http://localhost:8000
```

## Repository structure

```
sarm-spec/          — The SARM spec (prose + machine-readable schema)
sarm-ss-bridge/     — Source System bridge (FastAPI + SQLAlchemy)
sarm-inspector/     — Protocol inspector (single HTML file, Phase 5)
docs/               — Schema findings, interop run reports
```

## Phases

This toolkit is built in phases (see [CLAUDE.md](CLAUDE.md)). Current phase: **Phase 2 — Bridge skeleton**.

## Security notes

- All SQL queries use parameterized values — never string-interpolated input.
- Decision actions default to DRY-RUN — actual writes require explicit config.
- Bearer tokens are for testing only — SARM is auth-agnostic (§8 of the spec).
- Never commit real tokens or secrets.
