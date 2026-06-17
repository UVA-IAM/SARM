"""SQLAlchemy Core query runner.

Connects to any SQLAlchemy-supported database via a connection string.
Uses SQLAlchemy Core (not ORM) — builds and executes parameterized queries.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("sarm.bridge.sql")


class SQLSource:
    """Runs parameterized SQL queries against a configured database."""

    def __init__(self, database_url: str) -> None:
        self._engine: Engine = create_engine(database_url, echo=False)
        logger.info("SQLSource created — database: %s", _redact_url(database_url))

    @property
    def engine(self) -> Engine:
        return self._engine

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute a SELECT query and return rows as dicts.

        Uses SQLAlchemy Core text() for parameterized queries — never
        string-interpolates user or config input into SQL (CLAUDE.md §6.3).

        Args:
            sql: Parameterized SQL with :name placeholders.
            params: Mapping of parameter names to values.

        Returns:
            List of row dicts keyed by column name.
        """
        params = params or {}
        logger.info("SQL query — %s [params: %s]", sql.strip()[:120], ", ".join(params.keys()) or "(none)")
        with self._engine.connect() as conn:
            result = conn.execute(text(sql), params)
            rows = result.fetchall()
            logger.info("SQL query returned %d row(s)", len(rows))
            # Convert to list of dicts; handle both key names and SQLAlchemy Row objects
            return [dict(row._mapping) for row in rows]

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> int:
        """Execute a write/DDL statement and commit.

        Returns the number of affected rows.

        Args:
            sql: Parameterized SQL statement.
            params: Mapping of parameter names to values.

        Raises:
            Exception: On any database error.
        """
        params = params or {}
        logger.info("SQL execute (write/DDL) — %s [params: %s]", sql.strip()[:120], ", ".join(params.keys()) or "(none)")
        with self._engine.connect() as conn:
            result = conn.execute(text(sql), params)
            conn.commit()
            logger.info("SQL execute affected %d row(s)", result.rowcount)
            return result.rowcount

    def execute_in_txn(self, statements: list[tuple[str, dict[str, Any]]]) -> list[int]:
        """Execute multiple statements in a single transaction.

        Each statement is a (sql, params) tuple. All succeed or all roll back.

        Returns:
            List of rowcount per statement.
        """
        logger.info("SQL execute_in_txn — %d statement(s)", len(statements))
        for i, (sql, params) in enumerate(statements):
            logger.info("  stmt[%d] — %s [params: %s]", i, sql.strip()[:120], ", ".join(params.keys()) if params else "(none)")
        with self._engine.connect() as conn:
            counts = []
            for sql, params in statements:
                result = conn.execute(text(sql), params or {})
                counts.append(result.rowcount)
            conn.commit()
            logger.info("SQL execute_in_txn completed — rowcounts: %s", counts)
            return counts

    def close(self) -> None:
        """Dispose the engine connection pool."""
        logger.info("SQLSource closing — disposing engine")
        self._engine.dispose()


def _redact_url(url: str) -> str:
    """Redact passwords from database URLs for logging.

    Never log the full connection string with credentials (CLAUDE.md §6.3).
    """
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    # Check for user:pass@ pattern
    if "@" in rest:
        before, after = rest.rsplit("@", 1)
        if ":" in before:
            # Has username:password — redact password
            user = before.split(":", 1)[0]
            return f"{scheme}://{user}:***@{after}"
        return f"{scheme}://{before}:@...{after}"
    return url
