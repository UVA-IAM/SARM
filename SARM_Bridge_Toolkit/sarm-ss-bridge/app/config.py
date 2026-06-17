"""Configuration loader for the SARM bridge.

Loads YAML configuration from the path given by SARM_CONFIG env var
(defaults to config.yaml in the working directory), then overlays
environment-variable secrets on top.

Environment variables:
    SARM_CONFIG          Path to YAML config (default: config.yaml)
    SARM_DATABASE_URL    SQLAlchemy connection string (overrides yaml)
    SARM_BEARER_TOKEN    Bearer token for local testing (optional)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from pydantic import BaseModel, Field


class DatabaseConfig(BaseModel):
    """Database connection configuration."""

    url: str = Field(
        default="sqlite:////app/data/sample.sqlite",
        description="SQLAlchemy connection string. Overrides SARM_DATABASE_URL env var.",
    )


class ScopeQueryConfig(BaseModel):
    """A single scope query definition.

    Maps rows from a datasource table into SARM ScopeItems.
    """

    name: str = Field(description="Human-readable name for this query definition.")
    sql: str = Field(description="SQL query that returns scope item rows.")
    subject_id_column: str = Field(description="Column name that provides subjectId.")
    subject_label_column: str = Field(description="Column name that provides subjectLabel.")
    certifier_hint_column: str = Field(description="Column name that provides certifierHint.")
    decision_options_column: str = Field(
        default="decision_options",
        description="Column name that provides decisionOptions as JSON.",
    )
    resource_id_column: str | None = Field(
        default=None,
        description="Optional column name for resourceId grouping key.",
    )
    resource_label_column: str | None = Field(
        default=None,
        description="Optional column name for resourceLabel.",
    )
    decision_prompt_column: str | None = Field(
        default=None,
        description="Optional column name for decisionPrompt.",
    )
    context_data_columns: list[str] = Field(
        default_factory=list,
        description="Optional column names to include as contextData.",
    )


class DecisionActionConfig(BaseModel):
    """How to handle a Decision for a given decision option value.

    action: what the bridge should do
        - "none": no automated action (record only)
        - "write": execute a remediation SQL statement
        - "dry-run": log what would happen without executing
    sql: (only for action="write") parameterized SQL to execute.
    """

    value: str = Field(description="Decision option value this action maps to.")
    action: str = Field(
        default="none",
        description='Action to take: "none", "write", or "dry-run". Defaults to "none".',
    )
    sql: str | None = Field(
        default=None,
        description="Parameterized SQL statement to execute when action='write'.",
    )


class SourceConfig(BaseModel):
    """Complete source system configuration."""

    database: DatabaseConfig = Field(
        default_factory=lambda: DatabaseConfig(),
        description="Database connection.",
    )
    scope_queries: list[ScopeQueryConfig] = Field(
        default_factory=list,
        description="Scope query definitions.",
    )
    decision_actions: list[DecisionActionConfig] = Field(
        default_factory=list,
        description="Decision-to-action mappings.",
    )
    # Conformance level advertised in capability exchange (§4.6)
    conformance_level: int = Field(
        default=2,
        ge=1,
        le=3,
        description="Conformance level: 1=basic, 2=full, 3=push.",
    )
    # Whether to actually send decisions or just log them
    dry_run_decisions: bool = Field(
        default=True,
        description="When True, decisions are logged but not applied to the database.",
    )
    # Decision response mode: "sync" returns 201/200 immediately;
    # "async" returns 202 Accepted and expects the AE to await a
    # completion event (remediation.confirmed) via the return channel.
    # Overridden by SARM_DECISIONS_SYNC_MODE env var.
    decisions_sync_mode: str = Field(
        default="sync",
        description='Response mode for decisions: "sync" or "async".',
    )
    # Whether to accept a new decision for a scopeItemId that already
    # has a recorded decision. "allow" accepts the new decision (201).
    # "disallow" returns 409 Conflict — the Source System does not
    # permit decision supersession for this item.
    # Overridden by SARM_DECISIONS_REPLAY_DISPOSITION env var.
    decisions_replay_disposition: str = Field(
        default="allow",
        description='Disposition of a new decision for an already-decided item: "allow" or "disallow".',
    )


# Module-level singleton — set once at application startup
_loaded_config: SourceConfig | None = None


def load_config(path: str | None = None) -> SourceConfig:
    """Load and return SourceConfig from YAML + environment overrides.

    Also stores the config as the module-level singleton so that
    other modules can access it via get_config().

    Args:
        path: Path to the YAML config file. Defaults to SARM_CONFIG env var
              or "config.yaml" in the working directory.

    Returns:
        Validated SourceConfig.
    """
    global _loaded_config
    config_path = Path(path or os.environ.get("SARM_CONFIG", "config.yaml"))

    if not config_path.is_file():
        # Return defaults when no config file exists — useful for initial bootstrap
        _loaded_config = SourceConfig()
        return _loaded_config

    with open(config_path, "r") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    config = SourceConfig.model_validate(raw)

    # Environment-variable overrides for secrets and operational toggles
    db_url = os.environ.get("SARM_DATABASE_URL")
    if db_url:
        config.database.url = db_url

    sync_mode = os.environ.get("SARM_DECISIONS_SYNC_MODE")
    if sync_mode:
        if sync_mode not in ("sync", "async"):
            raise ValueError(
                f"SARM_DECISIONS_SYNC_MODE must be 'sync' or 'async', got '{sync_mode}'"
            )
        config.decisions_sync_mode = sync_mode

    replay_disp = os.environ.get("SARM_DECISIONS_REPLAY_DISPOSITION")
    if replay_disp:
        if replay_disp not in ("allow", "disallow"):
            raise ValueError(
                f"SARM_DECISIONS_REPLAY_DISPOSITION must be 'allow' or 'disallow', got '{replay_disp}'"
            )
        config.decisions_replay_disposition = replay_disp

    dry_run = os.environ.get("SARM_DECISIONS_DRY_RUN")
    if dry_run:
        config.dry_run_decisions = dry_run.lower() not in ("false", "0", "no")

    _loaded_config = config
    return config


def get_config() -> SourceConfig:
    """Return the loaded configuration singleton.

    Must be called after load_config() has been run (typically during
    application startup). Returns a default SourceConfig if load_config()
    was never called.
    """
    global _loaded_config
    if _loaded_config is None:
        _loaded_config = SourceConfig()
    return _loaded_config
