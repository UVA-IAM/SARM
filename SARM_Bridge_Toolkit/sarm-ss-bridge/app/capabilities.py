"""Capability Exchange surface (§4.7).

Implements:
    POST /sarm/v1/capabilities — exchange capabilities

The Source System responds with its capabilities (conformance level,
async support, event vocabulary, signing config).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.config import get_config
from app.models import Capabilities, SigningConfig
from app.return_channel import get_bearer_token, get_store

logger = logging.getLogger("sarm.bridge.capabilities")

router = APIRouter(prefix="/sarm/v1", tags=["capabilities"])


@router.post("/capabilities", response_model=None)
async def exchange_capabilities(request: Request):
    """Capability Exchange (§4.7).

    The Attestation Engine sends its capabilities; the Source System
    responds with its own.  If the AE provides a returnChannel and a
    bearer token is present, the returnChannel is stored keyed by the
    token so future requests can use it to decide async behaviour.

    Configuration-time handshake, not per-request.
    """
    config = get_config()

    # Parse the AE's capabilities (validates schemas field, etc.)
    body = await request.json()
    try:
        ae_caps = Capabilities.model_validate(body)
    except Exception:
        # Fall back to raw dict for backwards compat with non-conformant clients
        ae_caps = Capabilities.model_validate({"schemas": [], "role": body.get("role", "unknown")})

    # Store the AE's returnChannel keyed by bearer token (§4.7)
    bearer_token = get_bearer_token(request)
    store = get_store()
    if bearer_token and ae_caps.return_channel is not None and store is not None:
        store.store(bearer_token, ae_caps.return_channel)

    # Log what the AE sent (for debugging; never log tokens)
    ae_role = ae_caps.role
    logger.info("Capability exchange from AE role=%s", ae_role)

    # Effective mode determines whether async is possible.
    # Per §4.7: if the AE did not supply a returnChannel, the SS has no
    # address to deliver events to, so async is impossible — revert to
    # sync mode regardless of config.
    ae_return_channel = body.get("returnChannel")
    if ae_return_channel is None:
        # No return channel from the AE: we cannot deliver events, so
        # async is off and supportsAsync must be false.
        effective_mode = "sync"
        supports_async = False
    else:
        # AE provided a return channel — honor the configured mode.
        effective_mode = config.decisions_sync_mode
        supports_async = effective_mode == "async"

    # The "events" field declares what the SS will *push* to the AE.
    # When effective mode is sync there is no push path, so omit events
    # entirely (the spec says its presence signals async event delivery
    # per §4.7 / §5.4).
    events = (
        ["remediation.confirmed", "scope.discovered"]
        if supports_async
        else None
    )

    # Build the Source System's capability response
    response = Capabilities(
        role="sourceSystem",
        conformance_level=config.conformance_level,
        supports_async=supports_async,
        supports_conditional_get=True,  # we support ETag-based conditional GET
        events=events,
    )

    logger.info(
        "Capability response: conformance=L%d, async=%s, conditional=%s",
        config.conformance_level,
        response.supports_async,
        response.supports_conditional_get,
    )

    return response.model_dump(by_alias=True, exclude_none=True)
