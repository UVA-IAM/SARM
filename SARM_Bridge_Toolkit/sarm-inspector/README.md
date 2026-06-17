# SARM Inspector

A single-page protocol inspector for SARM endpoints. No build step, no CDN, no dependencies — just a file you open in a browser.

**Quick start:** see the [top-level README](../README.md).

## What it does

The inspector is a developer/QA tool for probing SARM interoperability. It wires itself to any SARM Source System, discovers scope items, lets you act on one, and shows the raw HTTP traffic the whole way.

It is **not** an end-user application. It's protocol plumbing — raw, observable, debuggable.

## How to use

### 1. Open the file

```
Open sarm-inspector/index.html in a browser.
```

That's it. No server, no `npm install`, no proxy. It runs entirely client-side.

### 2. Configure the endpoint

| Field | Description |
|---|---|
| **Base URL** | The bridge or Source System URL, e.g. `http://localhost:8080` |
| **Bearer Token** | Optional — included in the `Authorization` header if provided |

Click **Discover ScopeItems** to fetch items, or **Exchange Capabilities** first to query the Source System's advertised conformance.

### 3. Discover scope items

The inspector calls `GET /sarm/v1/ScopeItems` and renders the results as a table:

| Column | Content |
|---|---|
| ID | ScopeItem identifier |
| Subject | The object being attested |
| Certifier | Who must attest (from `certifierHint`) |
| Decision Options | Chips showing allowed decisions (SS-declared) |

Click any row to inspect the item's details.

### 4. Inspect an item

The detail view shows:

- **Identity fields** — ID, subject ID/label, certifier, resource ID/label
- **Decision prompt** — the question the certifier is answering
- **Decision options** — all allowed decisions with labels (from `decisionOptions` on the ScopeItem)
- **Context data** — human-readable extra fields (e.g. `memberSince`, `lastAccessAt`)
- **Meta** — creation/modification timestamps

### 5. Submit a decision

The decision form is populated from the selected item:

| Field | Source |
|---|---|
| **Decision** | Dropdown populated from the item's `decisionOptions` — **never hard-coded** |
| **Certifier ID** | Pre-filled from the item's `certifierHint` |
| **Channel** | Select from `web`, `email`, `intercept`, `bulk`, `default_action` |
| **Comment** | Optional free-text |

**Dry-run mode** (checked by default) shows the exact request that *would* be sent without sending it. Uncheck to actually POST to `/sarm/v1/Decisions`. After a dry-run preview, click **"Actually Send"** to override.

The inspector uses **decision idempotency** (SARM §5.4): the same `(item, decision)` pair reuses the same decision ID on retries, so the Source System can recognise replays (200 OK) versus new decisions (201 Created).

### 6. Watch the traffic

Every HTTP request and response is logged in collapsible accordions, visible from all three views:

- **Config view** — traffic from capabilities exchange and initial discovery
- **List view** — traffic from scope discovery (including pagination)
- **Detail view** — traffic from decision submission

Each accordion entry shows:

- Method and path
- Request headers (bearer token masked as `***`) and body
- Response status code, timing (ms), and body (pretty-printed JSON)

Toggle the traffic log with **"Show/Hide Traffic Log"** buttons.

## Views

```
Config → List → Detail
  ↑       ↓       ↓
```

- **Config** — endpoint setup, capabilities exchange, initial discovery
- **List** — scope items table, pagination, refresh
- **Detail** — item details, decision form, traffic log

Press **Escape** to go back one view. Click **"Change endpoint"** or **"Back to ScopeItems"** to navigate.

## Capabilities Exchange

Click **"Exchange Capabilities"** to POST to `/sarm/v1/capabilities`. The default request body:

```json
{
  "schemas": ["urn:ietf:params:sarm:api:messages:1.0:Capabilities"],
  "role": "attestationEngine"
}
```

Click **"Edit Capabilities Request Body"** to customize the payload. Check **"Include returnChannel"** to add a callback URL (also auto-generates a placeholder bearer token, since the return channel may require auth).

## Security

- **Bearer token stored in memory only** — never persisted, never sent outside the configured endpoint.
- **Token masked in traffic logs** — shown as `***` in the accordion display.
- **Dry-run by default** — decisions are previewed before sending.
- **No CDN, no external calls** — everything runs offline from a single file.

## Architecture

```
sarm-inspector/
└── index.html   — 980 lines: CSS + HTML + vanilla JS, zero dependencies
```

Three views managed by toggling `.hidden` classes:

1. **Config view** (`#config-view`) — URL/token form, capabilities, discover
2. **List view** (`#list-view`) — scope items table, pagination, refresh
3. **Detail view** (`#detail-view`) — item details, decision form, traffic log

State lives in a single `state` object:

```js
state = {
  baseUrl: '',        // configured endpoint
  token: '',          // bearer token (memory only)
  scopeItems: [],     // current page of ScopeItem[]
  listResponse: null, // full ListResponse from discovery
  currentItem: null,  // selected ScopeItem
  currentPage: 1,     // pagination
  totalCount: 0,
  traffic: [],        // { label, request, response }[]
  capabilities: null, // SS capabilities response
  lastDecisionId: null, // idempotency key (SARM §5.4)
}
```

The only HTTP helper is `httpFetch(path, options)` — a thin wrapper around `fetch()` that:

- Sets `Accept: application/scim+json`
- Adds `Authorization: Bearer <token>` if configured
- Serializes the request body to JSON
- Logs every request/response to `state.traffic`
- Masks tokens in the traffic log

## Protocol surfaces used

| Surface | Method | Path | SARM section |
|---|---|---|---|
| Capability Exchange | `POST` | `/sarm/v1/capabilities` | §4.7 |
| Scope Discovery | `GET` | `/sarm/v1/ScopeItems` | §4 |
| Decision Notification | `POST` | `/sarm/v1/Decisions` | §5 |

Conforms to SARM draft v0.3.
