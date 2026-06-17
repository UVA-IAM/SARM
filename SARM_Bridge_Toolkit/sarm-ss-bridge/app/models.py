"""Pydantic v2 models aligned with sarm-spec/schema.

These models mirror the JSON Schema definitions but are kept separate from
the schema files themselves — the two programs (bridge + inspector) share
conformance to the schema, not runtime code (CLAUDE.md §6.1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Common envelope ──


class Meta(BaseModel):
    """Common resource metadata per §3.2."""

    resource_type: str
    created: datetime | None = None
    last_modified: datetime | None = None
    version: str | None = None
    location: str | None = None


# ── ScopeItem (§3.3) ──


class DecisionOption(BaseModel):
    """One decision option per §3.3.1."""

    value: str
    label: str


class ScopeItem(BaseModel):
    """A single atomic attestation task per §3.3."""

    schemas: list[str] = Field(
        default_factory=lambda: [
            "urn:ietf:params:sarm:schemas:core:1.0:ScopeItem"
        ],
    )
    id: str
    meta: Meta
    subject_id: str = Field(alias="subjectId")
    subject_label: str = Field(alias="subjectLabel")
    certifier_hint: str = Field(alias="certifierHint")
    decision_options: list[DecisionOption] = Field(alias="decisionOptions")
    resource_id: str | None = Field(default=None, alias="resourceId")
    resource_label: str | None = Field(default=None, alias="resourceLabel")
    decision_prompt: str | None = Field(default=None, alias="decisionPrompt")
    context_data: dict[str, Any] | None = Field(default=None, alias="contextData")

    model_config = {
        "populate_by_name": True,
        "str_strip_whitespace": True,
    }


class ListResponse(BaseModel):
    """Paginated list response per §4.3."""

    schemas: list[str] = Field(
        default_factory=lambda: [
            "urn:ietf:params:sarm:api:messages:1.0:ListResponse"
        ],
    )
    total_results: int = Field(alias="totalResults")
    start_index: int = Field(alias="startIndex")
    items_per_page: int | None = Field(default=None, alias="itemsPerPage")
    resources: list[ScopeItem] = Field(alias="Resources")

    model_config = {
        "populate_by_name": True,
    }


# ── Decision (§3.4) ──


class Decision(BaseModel):
    """Records the outcome of attestation for a single ScopeItem per §3.4."""

    schemas: list[str] = Field(
        default_factory=lambda: [
            "urn:ietf:params:sarm:schemas:core:1.0:Decision"
        ],
    )
    id: str | None = None
    meta: Meta | None = None
    scope_item_id: str = Field(alias="scopeItemId")
    decision: str
    certifier_id: str = Field(alias="certifierId")
    decided_at: datetime = Field(alias="decidedAt")
    channel: str | None = None
    comment: str | None = None
    delegated_from: str | None = Field(default=None, alias="delegatedFrom")

    model_config = {
        "populate_by_name": True,
        "str_strip_whitespace": True,
    }


class BulkOperation(BaseModel):
    """A single operation within a BulkRequest (§5.2)."""

    method: str
    path: str
    data: Decision


class BulkRequest(BaseModel):
    """Batch decision notification per §5.2."""

    schemas: list[str] = Field(
        default_factory=lambda: [
            "urn:ietf:params:sarm:api:messages:1.0:BulkRequest"
        ],
    )
    operations: list[BulkOperation] = Field(alias="Operations")


# ── Error (§10) ──


class Error(BaseModel):
    """SARM Error object per §10."""

    schemas: list[str] = Field(
        default_factory=lambda: [
            "urn:ietf:params:sarm:api:messages:1.0:Error"
        ],
    )
    status: str
    scim_type: str = Field(alias="scimType")
    detail: str | None = None

    model_config = {
        "populate_by_name": True,
    }

    @classmethod
    def from_http(cls, status_code: int, scim_type: str, detail: str) -> Error:
        """Convenience factory from HTTP status components."""
        return cls(
            status=str(status_code),
            scim_type=scim_type,
            detail=detail,
        )


# ── Capabilities (§4.7) ──


class SigningConfig(BaseModel):
    """Message signing configuration per §11.6."""

    signs: bool = False
    requires_peer_signatures: bool = Field(default=False, alias="requiresPeerSignatures")
    algorithms: list[str] = Field(default_factory=list)
    key: dict[str, Any] | None = None


class Capabilities(BaseModel):
    """Capability Exchange message per §4.7."""

    schemas: list[str] = Field(
        default_factory=lambda: [
            "urn:ietf:params:sarm:api:messages:1.0:Capabilities"
        ],
    )
    role: str
    return_channel: dict[str, Any] | None = Field(default=None, alias="returnChannel")
    conformance_level: int | None = Field(default=None, alias="conformanceLevel")
    supports_async: bool | None = Field(default=None, alias="supportsAsync")
    supports_conditional_get: bool | None = Field(default=None, alias="supportsConditionalGet")
    events: list[str] | None = None
    signing: SigningConfig | None = None

    model_config = {
        "populate_by_name": True,
    }


def row_to_scopeitem(
    row: dict[str, Any],
    query_def: Any,  # ScopeQueryConfig — imported here to avoid circular deps
    base_url: str,
) -> ScopeItem:
    """Convert a database row into a ScopeItem resource.

    Uses the column mappings defined in the query configuration.
    Used by both discovery.py and decisions.py to avoid circular imports.
    """
    import json
    from datetime import timezone

    meta = Meta(
        resource_type="ScopeItem",
        created=datetime.now(timezone.utc),
        last_modified=datetime.now(timezone.utc),
        location=f"{base_url}/ScopeItems/{row['id']}",
    )

    # Parse decision options — stored as JSON in the database
    raw_options = row.get(query_def.decision_options_column, "[]")
    if isinstance(raw_options, str):
        options = json.loads(raw_options)
    else:
        options = raw_options

    decision_options = [
        DecisionOption(value=o["value"], label=o["label"])
        for o in options
    ]

    item = ScopeItem(
        id=row["id"],
        meta=meta,
        subject_id=row[query_def.subject_id_column],
        subject_label=row[query_def.subject_label_column],
        certifier_hint=row[query_def.certifier_hint_column],
        decision_options=decision_options,
    )

    # Optional fields
    if query_def.resource_id_column and row.get(query_def.resource_id_column):
        item.resource_id = row[query_def.resource_id_column]

    if query_def.resource_label_column and row.get(query_def.resource_label_column):
        item.resource_label = row[query_def.resource_label_column]

    if query_def.decision_prompt_column and row.get(query_def.decision_prompt_column):
        item.decision_prompt = row[query_def.decision_prompt_column]

    # Context data — check for a single JSON column first, then fall back
    # to individual columns if not present
    raw_context = row.get("context_data")
    if raw_context is not None:
        if isinstance(raw_context, str):
            try:
                item.context_data = json.loads(raw_context)
            except (json.JSONDecodeError, ValueError):
                item.context_data = raw_context
        else:
            item.context_data = raw_context
    elif query_def.context_data_columns:
        context_data = {}
        for col in query_def.context_data_columns:
            val = row.get(col)
            if val is not None:
                context_data[col] = val
        if context_data:
            item.context_data = context_data

    return item
