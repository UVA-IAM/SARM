"""FastAPI application — SARM Source System Bridge.

This is the entry point. Routes are registered here; business logic
lives in discovery.py and decisions.py.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import load_config
from app.decisions import _ensure_decisions_table, router as decisions_router
from app.discovery import router as discovery_router
from app.capabilities import router as capabilities_router
from app import return_channel

# Configure the sarm.bridge logger so all DB access logs are visible.
# Format: timestamp  level  logger_name  message
# Adopters can override with LOG_LEVEL env var (e.g. LOG_LEVEL=DEBUG).
_FORMAT = "%(asctime)s  %(levelname)-5s  %(name)s  %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
_root = logging.getLogger()
if not _root.handlers:
    _root.addHandler(_handler)
_root.setLevel(getattr(logging, os.environ.get("LOG_LEVEL", "INFO")))

logger = logging.getLogger("sarm.bridge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load config at startup; ensure decisions + return_channels tables exist."""
    config = load_config()
    _ensure_decisions_table(config.database.url)
    return_channel.init_store(config.database.url)
    logger.info(
        "Bridge started — database: %s, conformance: L%d, dry_run: %s, "
        "sync_mode: %s, replay_disposition: %s",
        config.database.url,
        config.conformance_level,
        config.dry_run_decisions,
        config.decisions_sync_mode,
        config.decisions_replay_disposition,
    )
    yield
    logger.info("Bridge shutting down")


app = FastAPI(
    title="SARM Source System Bridge",
    description=(
        "Source System endpoint for the SARM Interop Toolkit. "
        "Connects a datasource to the SARM Scope Discovery and "
        "Decision Notification surfaces."
    ),
    version="0.3.0",
    lifespan=lifespan,
)

class CORSAlwaysMiddleware(BaseHTTPMiddleware):
    """Guarantees CORS headers on ALL responses, including 500 errors.

    FastAPI's built-in CORSMiddleware may skip error responses that occur
    during exception handling. This middleware runs after the app and
    ensures the browser always sees CORS headers.
    """

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"
        return response


# Allow the inspector (a file:// page) to talk to this bridge
app.add_middleware(CORSAlwaysMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # file:// pages have origin "null"; * covers dev/QA
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Register in-scope routers
app.include_router(discovery_router)
app.include_router(decisions_router)
app.include_router(capabilities_router)


@app.get("/health")
async def health():
    """Liveness probe. Returns 200 when the bridge is running."""
    return {"status": "ok"}


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    """Catch-all: return a SARM Error for unhandled exceptions.

    CORS headers are added explicitly here because FastAPI's CORSMiddleware
    may not attach them to exceptions that occur during request body parsing
    or other early failures.
    """
    logger.exception("Unhandled exception: %s", exc)
    response = JSONResponse(
        status_code=500,
        content={
            "schemas": ["urn:ietf:params:sarm:api:messages:1.0:Error"],
            "status": "500",
            "scimType": "serverInternal",
            "detail": "An unexpected error occurred.",
        },
        media_type="application/scim+json",
    )
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"
    return response
