"""Decision Notification surface (§5).

Implements:
    POST /sarm/v1/Decisions — accept one or more decisions

Accepts a single Decision or a batch BulkRequest. Validates the decision
value against the ScopeItem's declared decisionOptions. Applies configured
actions (write/dry-run/none). Persists decisions to the decisions table.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_config
from app.models import (
    BulkRequest,
    Decision,
    row_to_scopeitem,
)
from app.return_channel import get_bearer_token, get_store
from app.sources.sql_source import SQLSource

logger = logging.getLogger("sarm.bridge.decisions")

router = APIRouter(prefix="/sarm/v1", tags=["decisions"])

# SQL to create the decisions table (used by the seed script too).
_CREATE_DECISIONS_TABLE = """
CREATE TABLE IF NOT EXISTS decisions (
    id              TEXT PRIMARY KEY,
    scope_item_id   TEXT NOT NULL,
    decision        TEXT NOT NULL,
    certifier_id    TEXT NOT NULL,
    decided_at      TEXT NOT NULL,
    channel         TEXT,
    comment         TEXT,
    delegated_from  TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(scope_item_id) REFERENCES scope_items(id)
);
CREATE INDEX IF NOT EXISTS idx_decisions_scope_item
    ON decisions(scope_item_id);
"""


def _ensure_decisions_table(url: str) -> SQLSource:
    """Create the decisions table if it does not exist. Returns a SQLSource."""
    logger.info("Decisions table — ensuring exists (database: %s)", _redact_url(url))
    src = SQLSource(url)
    try:
        # Execute CREATE TABLE and CREATE INDEX as separate statements;
        # SQLAlchemy's text() does not reliably handle multi-statement SQL.
        logger.info("Decisions table — CREATE TABLE decisions")
        src.execute(
            """CREATE TABLE IF NOT EXISTS decisions (
                id              TEXT PRIMARY KEY,
                scope_item_id   TEXT NOT NULL,
                decision        TEXT NOT NULL,
                certifier_id    TEXT NOT NULL,
                decided_at      TEXT NOT NULL,
                channel         TEXT,
                comment         TEXT,
                delegated_from  TEXT,
                created_at      TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(scope_item_id) REFERENCES scope_items(id)
            )""",
        )
        logger.info("Decisions table — CREATE INDEX idx_decisions_scope_item")
        src.execute(
            "CREATE INDEX IF NOT EXISTS idx_decisions_scope_item ON decisions(scope_item_id)",
        )
        logger.info("Decisions table — ready")
    except Exception:
        # Table may already exist or DB may not support foreign keys — ignore.
        logger.debug("decisions table ensure: %s", exc_info=True)
    return src


def _find_scope_item(source: SQLSource, item_id: str) -> dict | None:
    """Look up a ScopeItem row by id from the database.

    Returns the raw row dict (not a ScopeItem model) so we can pass it
    to row_to_scopeitem() without circular imports.
    """
    logger.info("DB — lookup ScopeItem id=%s", item_id)
    config = get_config()
    if not config.scope_queries:
        return None

    query_def = config.scope_queries[0]
    sql = query_def.sql.replace(";", "").rstrip()
    if "WHERE" in sql.upper():
        sql = sql + " AND id = :id"
    else:
        sql = sql + " WHERE id = :id"

    rows = source.query(sql, {"id": item_id})
    found = rows[0] if rows else None
    logger.info("DB — ScopeItem lookup id=%s → %s", item_id, "found" if found else "not found")
    return found


def _validate_decision_value(
    source: SQLSource,
    scope_item_id: str,
    decision_value: str,
) -> bool:
    """Check that the decision value is declared in the ScopeItem's decisionOptions (§3.3.1).

    Returns True if valid, raises HTTPException if not.
    """
    row = _find_scope_item(source, scope_item_id)
    if row is None:
        raise HTTPException(
            status_code=400,
            detail={
                "schemas": ["urn:ietf:params:sarm:api:messages:1.0:Error"],
                "status": "400",
                "scimType": "invalidValue",
                "detail": f"ScopeItem '{scope_item_id}' not found.",
            },
        )

    config = get_config()
    query_def = config.scope_queries[0]
    item = row_to_scopeitem(row, query_def, "http://localhost")

    valid_values = {opt.value for opt in item.decision_options}
    if decision_value not in valid_values:
        raise HTTPException(
            status_code=400,
            detail={
                "schemas": ["urn:ietf:params:sarm:api:messages:1.0:Error"],
                "status": "400",
                "scimType": "invalidValue",
                "detail": f"Decision value '{decision_value}' is not a declared option for ScopeItem {scope_item_id}. Declared options: {', '.join(sorted(valid_values))}.",
            },
        )

    return True


def _apply_action(source: SQLSource, decision: Decision) -> None:
    """Apply the configured action for this decision value.

    Actions are defined in config.decision_actions. The action with the
    matching decision value is selected; if none match, defaults to "none".
    """
    config = get_config()

    action_cfg = None
    for ac in config.decision_actions:
        if ac.value == decision.decision:
            action_cfg = ac
            break

    if action_cfg is None:
        logger.info("Decision %s: no action configured for value '%s', skipping", decision.id, decision.decision)
        return

    if action_cfg.action == "none":
        logger.info("Decision %s: action=none, recorded but not applied", decision.id)
        return

    if action_cfg.action == "dry-run":
        logger.info(
            "Decision %s: action=dry-run — would execute: %s",
            decision.id,
            action_cfg.sql,
        )
        return

    if action_cfg.action == "write":
        if config.dry_run_decisions:
            logger.info(
                "Decision %s: dry_run_decisions=True — would execute: %s",
                decision.id,
                action_cfg.sql,
            )
            return
        if action_cfg.sql:
            logger.info("Decision %s: executing action SQL (write mode): %s", decision.id, action_cfg.sql)
            source.execute(action_cfg.sql, {"scopeItemId": decision.scope_item_id, "decision": decision.decision})
            logger.info("Decision %s: action SQL completed", decision.id)
            return

    logger.warning("Decision %s: unknown action '%s'", decision.id, action_cfg.action)


@router.post("/Decisions", response_model=None)
async def submit_decisions(request: Request):
    """Submit one or more decisions (§5.1).

    Accepts a single Decision or a BulkRequest batch.
    Returns appropriate status codes per §5.4.

    When the AE's bearer token has a stored return channel (from the
    capability exchange) and async mode is enabled, returns 202.
    Otherwise returns 201/200 (sync).
    """
    body = await request.json()
    config = get_config()
    source = SQLSource(config.database.url)

    # Extract bearer token and look up any stored return channel
    bearer_token = get_bearer_token(request)
    return_channel = None
    if bearer_token:
        store = get_store()
        if store is not None:
            return_channel = store.lookup(bearer_token)

    try:
        if "Operations" in body:
            return _handle_batch(body, config, source, return_channel)
        else:
            return _handle_single(body, config, source, return_channel)

    finally:
        source.close()


def _handle_single(
    body: dict,
    config,
    source: SQLSource,
    return_channel: dict | None = None,
) -> dict:
    """Handle a single Decision POST.

    Args:
        body: Raw decision JSON.
        config: SourceConfig singleton.
        source: SQLSource for DB queries.
        return_channel: AE's return channel from the capability exchange
            (or None if no token / no stored channel).  When present and
            async mode is enabled, returns 202 Accepted.
    """
    decision_data = body.copy()
    if "schemas" not in decision_data:
        decision_data["schemas"] = ["urn:ietf:params:sarm:schemas:core:1.0:Decision"]

    if not decision_data.get("id"):
        decision_data["id"] = str(uuid.uuid4())

    if not decision_data.get("decidedAt"):
        decision_data["decidedAt"] = datetime.now(timezone.utc).isoformat()

    try:
        decision = Decision.model_validate(decision_data)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={
                "schemas": ["urn:ietf:params:sarm:api:messages:1.0:Error"],
                "status": "400",
                "scimType": "invalidSyntax",
                "detail": f"Decision is malformed: {e}",
            },
        )

    # Validate decision value against ScopeItem's declared options (§5.5)
    _validate_decision_value(source, decision.scope_item_id, decision.decision)

    # Idempotency check (§5.4): query DB for existing decision by id.
    # Must run BEFORE replay disposition so that same-id same-body replays
    # are treated as idempotent reads (200) even in disallow mode.
    logger.info("DB — idempotency check decision id=%s", decision.id)
    existing_rows = source.query(
        "SELECT * FROM decisions WHERE id = :did",
        {"did": decision.id},
    )
    if existing_rows:
        stored = existing_rows[0]
        # Verify body matches (scopeItemId + decision)
        if (
            stored["scope_item_id"] != decision.scope_item_id
            or stored["decision"] != decision.decision
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "schemas": ["urn:ietf:params:sarm:api:messages:1.0:Error"],
                    "status": "409",
                    "scimType": "mutateReadOnly",
                    "detail": f"Decision id '{decision.id}' reused with different body.",
                },
            )
        logger.info("Decision %s: idempotent replay from DB", decision.id)
        # Return the original decision in the standard format
        return JSONResponse(
            status_code=200,
            content=decision.model_dump(by_alias=True, mode="json"),
        )

    # Check whether this ScopeItem already has a recorded decision (§5.4).
    logger.info("DB — count prior decisions for scope_item_id=%s", decision.scope_item_id)
    prior = source.query(
        "SELECT COUNT(*) AS cnt FROM decisions WHERE scope_item_id = :sid",
        {"sid": decision.scope_item_id},
    )
    already_decided = bool(prior and prior[0]["cnt"] > 0)
    # By reaching here, the decision ID is confirmed new (idempotency check
    # above would have returned 200 or 409). Used to pick 201 vs 200 after insert.
    decision_id_was_new = True
    logger.info("DB — prior decisions for scope_item_id=%s: %d", decision.scope_item_id, prior[0]["cnt"] if prior else 0)

    # Replay disposition: reject a new decision for an already-decided item
    # (§5.4, item (b)) when config says disallow.
    if config.decisions_replay_disposition == "disallow" and already_decided:
        raise HTTPException(
            status_code=409,
            detail={
                "schemas": ["urn:ietf:params:sarm:api:messages:1.0:Error"],
                "status": "409",
                "scimType": "mutateReadOnly",
                "detail": (
                    f"ScopeItem '{decision.scope_item_id}' already has a recorded "
                    f"decision; this Source System does not permit supersession "
                    f"(decisions_replay_disposition=disallow)."
                ),
            },
        )

    # Apply configured action
    _apply_action(source, decision)

    # Persist to decisions table
    logger.info("DB — INSERT decision id=%s scope_item_id=%s", decision.id, decision.scope_item_id)
    source.execute(
        """INSERT INTO decisions
           (id, scope_item_id, decision, certifier_id, decided_at,
            channel, comment, delegated_from)
           VALUES (:id, :sid, :dec, :cid, :dat, :ch, :cmt, :df)""",
        {
            "id": decision.id,
            "sid": decision.scope_item_id,
            "dec": decision.decision,
            "cid": decision.certifier_id,
            "dat": decision.decided_at,
            "ch": decision.channel,
            "cmt": decision.comment,
            "df": decision.delegated_from,
        },
    )
    logger.info("DB — decision persisted id=%s", decision.id)

    # Decide response code per §5.4.
    # 202 Accepted: async mode AND the AE has a stored return channel
    #   (the bridge needs a delivery target to use 202 — §4.7 / §5.4).
    # 201 Created: first-time record in sync mode.
    # 200 OK: resubmission for an already-decided item.
    async_enabled = config.decisions_sync_mode == "async"
    if async_enabled and return_channel is not None:
        logger.info("Decision %s: accepted async (202) — return channel present", decision.id)
        return JSONResponse(
            status_code=202,
            content=decision.model_dump(by_alias=True, mode="json"),
        )

    if decision_id_was_new:
        logger.info("Decision %s: recorded (201)", decision.id)
        return JSONResponse(
            status_code=201,
            content=decision.model_dump(by_alias=True, mode="json"),
        )

    logger.info("Decision %s: resubmission for already-decided item (200)", decision.id)
    return JSONResponse(
        status_code=200,
        content=decision.model_dump(by_alias=True, mode="json"),
    )


def _handle_batch(
    body: dict,
    config,
    source: SQLSource,
    return_channel: dict | None = None,
) -> dict:
    """Handle a BulkRequest batch.

    Args:
        body: Raw BulkRequest JSON.
        config: SourceConfig singleton.
        source: SQLSource for DB queries.
        return_channel: AE's return channel (same for all ops in the batch).
    """
    try:
        bulk = BulkRequest.model_validate(body)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={
                "schemas": ["urn:ietf:params:sarm:api:messages:1.0:Error"],
                "status": "400",
                "scimType": "invalidSyntax",
                "detail": f"BulkRequest is malformed: {e}",
            },
        )

    # All operations in a batch share the same bearer token → same
    # return channel.  Determine the async status code once.
    async_enabled = config.decisions_sync_mode == "async"
    async_status = "202" if (async_enabled and return_channel is not None) else "201"

    # F1: BulkResponse shape is not yet defined by the spec (Q8).
    results = []
    for op in bulk.operations:
        try:
            _handle_single(
                op.data.model_dump(by_alias=True),
                config,
                source,
                return_channel,
            )
            results.append({"status": async_status, "id": op.data.id})
        except HTTPException as exc:
            results.append({
                "status": exc.status_code,
                "detail": exc.detail if isinstance(exc.detail, dict) else {},
            })

    # Return 200 with results — F1 notes the shape is TBD
    return {
        "schemas": ["urn:ietf:params:sarm:api:messages:1.0:BulkResponse"],
        "Operations": results,
    }


def _redact_url(url: str) -> str:
    """Redact passwords from database URLs for logging."""
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        before, after = rest.rsplit("@", 1)
        if ":" in before:
            user = before.split(":", 1)[0]
            return f"{scheme}://{user}:***@{after}"
        return f"{scheme}://{before}:@...{after}"
    return url
