"""Return-channel storage for the capability exchange (§4.7).

When an Attestation Engine (AE) provides a returnChannel during the
capability handshake, the Source System stores it keyed by the AE's
bearer token.  Future requests carrying that token can then look up
the channel to decide whether async (202) responses are appropriate
and, later, to deliver completion events.

If no return channel is found for a token the bridge assumes sync
behaviour (201/200), per the conservative default in §4.7.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

logger = logging.getLogger("sarm.bridge.return_channel")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS return_channels (
    bearer_token    TEXT PRIMARY KEY,
    return_channel  TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now'))
);
"""


class ReturnChannelStore:
    """Thin SQLite-backed store for AE return channels.

    The table lives in the same database as the scope_items / decisions
    tables.  We use raw sqlite3 (not SQLAlchemy) because this module is
    imported before the full SQLAlchemy engine lifecycle is established
    during startup, and the shape is simple enough that an ORM would be
    overkill.
    """

    def __init__(self, database_url: str) -> None:
        """
        Args:
            database_url: SQLAlchemy connection string.  For SQLite URLs
                (``sqlite:///...`` or ``sqlite:///:memory:``) we extract
                the file path and open a direct connection.
        """
        self._db_path = _extract_sqlite_path(database_url)
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Create the return_channels table if it does not exist."""
        logger.info("Return channel DB — ensuring table exists (path: %s)", self._db_path)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(_CREATE_TABLE_SQL)
            conn.commit()
            logger.info("Return channel DB — table verified")
        except Exception:
            logger.debug("return_channels table ensure: %s", exc_info=True)
        finally:
            conn.close()

    def store(self, bearer_token: str, return_channel: dict[str, Any]) -> None:
        """Store (or update) a return channel for the given bearer token.

        Uses INSERT OR REPLACE so re-handshakes are idempotent.
        """
        channel_json = json.dumps(return_channel)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO return_channels (bearer_token, return_channel) "
                "VALUES (:token, :channel)",
                {"token": bearer_token, "channel": channel_json},
            )
            conn.commit()
            logger.info("Stored return channel for bearer token '%s'", _redact(bearer_token))
        except Exception:
            logger.warning("Failed to store return channel: %s", exc_info=True)
        finally:
            conn.close()

    def lookup(self, bearer_token: str) -> dict[str, Any] | None:
        """Look up the return channel for a bearer token.

        Returns the parsed JSON object, or None if not found.
        """
        logger.info("Return channel DB — lookup for token '%s'", _redact(bearer_token))
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT return_channel FROM return_channels WHERE bearer_token = :token",
                {"token": bearer_token},
            ).fetchone()
            if row is None:
                logger.info("Return channel DB — lookup miss for token '%s'", _redact(bearer_token))
                return None
            result = json.loads(row[0])
            logger.info("Return channel DB — lookup hit for token '%s'", _redact(bearer_token))
            return result
        except (json.JSONDecodeError, Exception):
            logger.warning("Failed to parse stored return channel: %s", exc_info=True)
            return None
        finally:
            conn.close()


def _extract_sqlite_path(database_url: str) -> str:
    """Convert a SQLAlchemy SQLite URL to a filesystem path.

    SQLAlchemy encodes SQLite URLs as:
        sqlite:///:memory:           -> in-memory DB
        sqlite:///relative/path      -> 3 slashes (sqlite:// + /path)
        sqlite:////absolute/path     -> 4 slashes (sqlite:// + //path)

    For absolute paths (4+ slashes) we return an absolute path so that
    sqlite3.connect() resolves to the same file regardless of CWD.
    """
    if database_url.startswith("sqlite:///:memory:"):
        return ":memory:"

    # Count leading slashes after "sqlite://" to detect absolute vs relative
    remainder = database_url[len("sqlite://"):]
    leading = len(remainder) - len(remainder.lstrip("/"))

    if leading >= 2:
        # Absolute path: sqlite:////app/data/sample.sqlite
        # Keep one slash (making it /app/data/sample.sqlite)
        return "/" + remainder.lstrip("/")
    else:
        # Relative path: sqlite:///data/sample.sqlite
        # Strip leading slashes; handle ./ prefix
        path = remainder.lstrip("/")
        if path.startswith("./"):
            path = path[2:]
        return path


def _redact(token: str) -> str:
    """Return a short redacted form of the token for logging.

    Never log the full token (CLAUDE.md §6.3).
    """
    if len(token) <= 12:
        return "***"
    return f"{token[:6]}...{token[-4:]}"


# Module-level singleton — initialized in main.py lifespan()
_store: ReturnChannelStore | None = None


def init_store(database_url: str) -> None:
    """Create the module-level store (call once at startup)."""
    global _store
    _store = ReturnChannelStore(database_url)
    logger.info("Return channel store initialized — database: %s", database_url)


def get_store() -> ReturnChannelStore | None:
    """Return the module-level store, or None if not yet initialized."""
    return _store


def get_bearer_token(request: Any) -> str | None:
    """Extract the bearer token from the Authorization header.

    Returns None when no Authorization header or non-bearer scheme.
    Never returns an empty string — callers should treat None and ''
    the same (no token present).
    """
    auth = request.headers.get("authorization", "")
    if not auth:
        return None
    # FastAPI normalizes headers to lowercase
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return token if token else None
    return None
