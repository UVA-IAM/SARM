"""Scope Discovery surface (§4).

Implements:
    GET /sarm/v1/ScopeItems          — list scope items
    GET /sarm/v1/ScopeItems/{id}     — retrieve a single item

Maps database rows into SARM ScopeItem resources per §3.3.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Query, HTTPException, Request, Header
from fastapi.responses import JSONResponse

from app.config import get_config
from app.models import (
    ListResponse,
    ScopeItem,
    row_to_scopeitem,
)
from app.sources.sql_source import SQLSource

logger = logging.getLogger("sarm.bridge.discovery")

router = APIRouter(prefix="/sarm/v1", tags=["discovery"])

# SCIM filter regex — matches: fieldPath op "value"
# Supports: eq, ne, co, sw, pr (others logged but pass-through)
_FILTER_RE = re.compile(
    r'^([a-zA-Z_][a-zA-Z0-9_.]*)\s+(eq|ne|co|sw|pr)\s+"([^"]*)"$'
)


def _make_etag(row: dict[str, Any]) -> str:
    """Generate an opaque ETag for a database row (§4.5, §3.2).

    Uses MD5 of the row's id + serialized row data; sufficient for
    conditional retrieval — we do not need cryptographic strength.
    """
    raw = f"{row['id']}:{json.dumps(row, sort_keys=True, default=str)}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _parse_filter(expr: str) -> tuple[str, str, str] | None:
    """Parse a simple SCIM filter expression.

    Returns (field_path, operator, value) or None if the filter is
    unparseable (caller should return all results).
    """
    m = _FILTER_RE.match(expr.strip())
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def _get_field(obj: dict, path: str) -> Any:
    """Get a nested field from a dict using dot notation.

    e.g. "meta.created" -> obj["meta"]["created"]
    """
    parts = path.split(".")
    current = obj
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _evaluate_filter(item_dict: dict, field: str, op: str, value: str) -> bool:
    """Evaluate a single filter predicate against a ScopeItem dict.

    Supports eq, ne, co (contains), sw (starts with), pr (presence / non-null).
    """
    if op == "pr":
        return _get_field(item_dict, field) is not None

    field_val = _get_field(item_dict, field)
    if field_val is None:
        return False

    if op == "eq":
        return str(field_val) == value
    if op == "ne":
        return str(field_val) != value
    if op == "co":
        return value in str(field_val)
    if op == "sw":
        return str(field_val).startswith(value)

    return True


def _item_to_dict(item: ScopeItem) -> dict:
    """Convert a ScopeItem model to a plain dict for filtering."""
    return item.model_dump(by_alias=True, mode='json')


def _apply_attributes(data: dict, attr_str: str) -> dict:
    """Prune a resource dict to only the requested SCIM attributes (§4.2).

    Supports top-level names and dotted paths like "meta.created".
    Always preserves "schemas" (required envelope field).
    """
    if not attr_str:
        return data

    requested = [a.strip() for a in attr_str.split(",") if a.strip()]
    result: dict[str, Any] = {"schemas": data.get("schemas", [])}

    for attr in requested:
        if "." in attr:
            # Nested attribute: e.g. "meta.created"
            parts = attr.split(".", 1)
            top = parts[0]
            if top in data and isinstance(data[top], dict):
                nested = {parts[1]: data[top][parts[1]]} if parts[1] in data[top] else {}
                if top in result:
                    result[top].update(nested)
                else:
                    result[top] = nested
        elif attr in data:
            result[attr] = data[attr]

    return result


def _json_response(content: Any, status_code: int = 200) -> JSONResponse:
    """Return a JSONResponse with the SARM-conformant Content-Type (§4.3)."""
    return JSONResponse(
        content=content,
        status_code=status_code,
        media_type="application/scim+json",
    )


def _get_base_url(request: Request) -> str:
    """Construct the base URL from the request."""
    return f"{request.url.scheme}://{request.url.hostname}"


def _fetch_items(
    source: SQLSource,
    query_def: Any,
    base_url: str,
    item_id: str | None = None,
) -> list[dict]:
    """Execute the scope query, optionally filtered by item_id."""
    sql = query_def.sql.replace(";", "").rstrip()
    if item_id:
        if "WHERE" in sql.upper():
            sql = sql + " AND id = :id"
        else:
            sql = sql + " WHERE id = :id"
        return source.query(sql, {"id": item_id})
    logger.info("DB — scope query (all items)")
    return source.query(sql)


@router.get("/ScopeItems", response_model=None)
async def list_scope_items(
    request: Request,
    filter: str | None = Query(default=None, description="SCIM-style filter expression (§4.4)"),
    startIndex: int = Query(default=1, ge=1, description="1-based pagination index (§4.2)"),
    count: int = Query(default=100, ge=1, le=1000, description="Page size (§4.2)"),
    attributes: str | None = Query(default=None, description="Comma-separated attributes to return (§4.2)"),
):
    """List ScopeItems needing attestation (§4.1).

    Returns a paginated ListResponse per §4.3.
    """
    config = get_config()
    source = SQLSource(config.database.url)

    try:
        if not config.scope_queries:
            return _json_response(ListResponse(
                total_results=0,
                start_index=startIndex,
                items_per_page=count,
                resources=[],
            ).model_dump(by_alias=True, mode='json'))

        query_def = config.scope_queries[0]
        base_url = _get_base_url(request)

        # Exclude items that already have a recorded decision.
        # Once a certifier has decided on an item, it is no longer
        # "needing attestation" and should not reappear in discovery.
        logger.info("DB — querying decided items to exclude")
        decided_ids: set[str] = {
            r["scope_item_id"]
            for r in source.query(
                "SELECT DISTINCT scope_item_id FROM decisions",
            )
        }
        if decided_ids:
            logger.debug("Discovery: %d already-decided items to exclude", len(decided_ids))

        # Apply SCIM-style filter (§4.4) — client-side for now
        filtered_items: list[ScopeItem] = []
        rows = _fetch_items(source, query_def, base_url)

        for row in rows:
            # Skip items that already have a recorded decision
            if row["id"] in decided_ids:
                logger.debug("Discovery: excluding decided item %s", row["id"])
                continue

            item = row_to_scopeitem(row, query_def, base_url)
            # Inject ETag version (§4.5)
            if item.meta:
                item.meta.version = _make_etag(row)

            if filter:
                item_dict = _item_to_dict(item)
                parsed = _parse_filter(filter)
                if parsed:
                    field, op, value = parsed
                    if _evaluate_filter(item_dict, field, op, value):
                        filtered_items.append(item)
                    else:
                        logger.debug("Filter '%s' excluded %s", filter, item.id)
                else:
                    logger.warning("Filter '%s' — unparseable, returning all", filter)
                    filtered_items.append(item)
            else:
                filtered_items.append(item)

        # Pagination (§4.2)
        total = len(filtered_items)
        end = min(startIndex - 1 + count, total)
        page = filtered_items[startIndex - 1:end] if startIndex > 0 else filtered_items[:end]

        logger.info("Discovery: returned %d of %d ScopeItems (page %d)", len(page), total, startIndex)

        # Apply attribute selection (§4.2)
        if attributes:
            page = [
                _apply_attributes(item.model_dump(by_alias=True, mode='json'), attributes)
                for item in page
            ]
            # Update ListResponse with pruned resources
            list_data: dict[str, Any] = {
                "schemas": ["urn:ietf:params:sarm:api:messages:1.0:ListResponse"],
                "totalResults": total,
                "startIndex": startIndex,
                "Resources": page,
            }
            if count if end < total else None:
                list_data["itemsPerPage"] = count if end < total else None
            return _json_response(list_data)

        return _json_response(ListResponse(
            total_results=total,
            start_index=startIndex,
            items_per_page=count if end < total else None,
            resources=page,
        ).model_dump(by_alias=True, mode='json'))

    finally:
        source.close()


@router.get("/ScopeItems/{item_id}", response_model=None)
async def get_scope_item(
    request: Request,
    item_id: str,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    attributes: str | None = Query(default=None),
):
    """Retrieve a single ScopeItem by id (§4.5).

    Supports conditional retrieval via If-None-Match (ETag).
    """
    config = get_config()
    source = SQLSource(config.database.url)

    try:
        if not config.scope_queries:
            raise HTTPException(
                status_code=404,
                detail={
                    "schemas": ["urn:ietf:params:sarm:api:messages:1.0:Error"],
                    "status": "404",
                    "scimType": "noSuchScopeItem",
                    "detail": "No ScopeItems found — no scope queries configured.",
                },
            )

        query_def = config.scope_queries[0]
        base_url = _get_base_url(request)
        logger.info("DB — scope query (single item) id=%s", item_id)
        rows = _fetch_items(source, query_def, base_url, item_id=item_id)

        if not rows:
            logger.info("DB — ScopeItem %s not found in scope", item_id)
            raise HTTPException(
                status_code=404,
                detail={
                    "schemas": ["urn:ietf:params:sarm:api:messages:1.0:Error"],
                    "status": "404",
                    "scimType": "noSuchScopeItem",
                    "detail": f"ScopeItem '{item_id}' is not in scope.",
                },
            )

        item = row_to_scopeitem(rows[0], query_def, base_url)

        # Inject ETag version (§4.5)
        if item.meta:
            item.meta.version = _make_etag(rows[0])

        # Conditional retrieval — check ETag (§4.5)
        if if_none_match and item.meta and item.meta.version:
            etag = item.meta.version
            weak_etag = f'W/"{etag}"'
            if if_none_match == etag or if_none_match == weak_etag:
                response = JSONResponse(content={}, status_code=304, media_type="application/scim+json")
                response.headers["ETag"] = etag
                return response

        item_dict = item.model_dump(by_alias=True, mode='json')
        if attributes:
            item_dict = _apply_attributes(item_dict, attributes)
        return _json_response(item_dict)

    finally:
        source.close()
