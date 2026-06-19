# SARM: System for Attestation and Recertification Management

*One-page explainer — working draft v0.3, June 2026*

## The problem

Every system that needs periodic access review builds its own. Managers recertify access in one tool, group owners attest to membership in another, service owners confirm usage in a third. The mechanics — define scope, assign a certifier, record the decision, act on it — are identical everywhere, yet each implementation is bespoke. SARM proposes to do for attestation what SCIM did for provisioning: one common data model and a small set of REST endpoints any system implements once.

## The model

SARM models one thing: *an identity answers a question about one or more objects within its scope.* Concretely, two cooperating roles, plus a consumer:

- **Attestation Engine (AE)** — runs campaigns: defines populations, assigns certifiers, collects decisions. Campaigns are internal to the AE and never cross the wire.
- **Source System (SS)** — owns the things being attested (a Grouper group, an entitlement store, a service registry) and acts on the outcome.
- **Identity Provider (IdP)** — optionally asks, mid-login, whether a user has attestations pending.

The unit of work is a **ScopeItem**: one certifier, one question, one subject, with an SS-declared list of acceptable answers. A real-world review of 25 group members is 25 ScopeItems sharing a grouping key. A **Decision** records the answer to one ScopeItem.

## The interactions

1. **Scope Discovery** — the AE pulls ScopeItems from the SS (`GET /ScopeItems`, SCIM-style filtering and paging).
2. **Decision Notification** — the AE pushes Decisions back to the SS (`POST /Decisions`); the SS acts internally. Remediation itself is *not* a SARM call.
3. **Intercept Check** — the IdP asks the AE for a single action verdict (`none` / `redirectOptional` / `redirectRequired`) during authentication, and must fail open.

## Establishing the relationship

Underneath the three interactions sits an optional **Capability Exchange** (`POST /Capabilities`): a configuration-time handshake — one record per peer, not a per-request call — by which an AE and SS establish how they will work together, and the foundation the spec's optional behaviors rest on. It is deliberately asymmetric, mirroring the relationship. The SS declares what the AE must adapt to: its conformance level, whether it processes decisions asynchronously, whether it honors conditional retrieval, and the event types it will emit. The AE declares only a **return channel** — where the SS may deliver events — whose presence signals it can receive asynchronous notification at all, plus its key if signing is used. Three things hang off this handshake: asynchronous decision processing (an SS may accept a Decision with `202` and confirm completion later, but only over an established return channel); the **event** channel that carries those confirmations and other SS→AE notifications (a standard envelope, with the SS owning the vocabulary); and optional **message signing** (application-layer JWS over the canonicalized message, with the pinned algorithm and each party's key agreed here). The exchange is optional — two parties that share this configuration out of band may skip it, and conservative defaults then apply.

## Design stance

- **Minimal wire surface.** A message carries only what the receiver must act on — no campaign references, no subject classification, no internal state.
- **Vocabulary lives at the edges.** The SS owns its decision values and its event types; SARM standardizes the envelope, not the words.
- **SARM is not in the auth trust chain.** Intercept Check unavailability must never block login.

## What's in this repo

- **`SARM_Specification_Draft_v0.3.txt`** — the full draft specification.
- **`SARM_Bridge_Toolkit/`** — a working proof that a SARM round-trip runs end to end on real data: a bridge that exposes an existing SQL or LDAP datasource as a SARM Source System, and a protocol inspector that drives the round-trip and shows the raw wire traffic. It is a proof and conformance probe, not a production engine (a fuller open-source Attestation Engine, ARMS, is developed separately), and its findings feed back into the draft. See its own `README.md`.

## Open — needs resolution

*Ordered by stakes. Bracketed tags map to the draft's own open questions.*

1. **Async-completion event vs. open event vocabulary** (§5.4 / §7.2). The completion signal for a 202'd Decision is the one protocol-load-bearing event, yet it lives in undefined-vocabulary territory under a "MUST ignore unrecognized type" rule, with its correlation key (`scopeItemId`) buried in the opaque `data` payload. Decide whether this is a distinguished, defined message rather than "just another event."

2. **Message signing (§11.6) — the bones are right, but five things must be pinned down before it is implementable:**
   - *Where the JWT rides.* "Carried alongside the resource" is never made concrete. Pick one home (e.g. a SARM signature HTTP header) used identically for ScopeItems, Decisions, and events.
   - *Canonicalization safety.* RFC 8785 only yields identical bytes on I-JSON-clean input. Mandate RFC 7493 (no duplicate keys, bounded numbers) and reject non-conforming input before hashing — or carry the resource as the JWS payload and drop detachment.
   - *Replay.* `iat` plus a freshness window does not stop replay without single-use tracking. Add a `jti`/nonce and require receivers to retain *ids, not bodies* for the window, reconciling with the no-retention default.
   - *Key identification.* No `kid`, so rotation overlap is unhandled. Add one.
   - *Issuer check.* `iss` is signed but never required to equal the expected peer.

3. **Interop floors for signing.** Mandate a baseline algorithm so two signers always share one — ES256 recommended [Q9] — and give a recommended freshness window / skew tolerance, which has the same silent-mismatch failure mode as the algorithm question.

4. **Capability Exchange shape** [Q7, shape left open]. A single `POST` conflates SS capability *discovery* (GET-shaped, static, cacheable) with AE *registration* (return channel + key). Consider splitting them, and define a lifecycle for re-establishment and key rotation.

5. **202 guidance inconsistency.** §5.4 says SHOULD NOT return 202 without a return channel; §4.7 says MUST NOT. Align them (MUST NOT is right) or state the distinction explicitly.

6. **BulkResponse shape** [Q8]. Undefined; SCIM 7644 §3.7 with per-operation status is the likely path. Settle partial-batch-failure reporting.

7. **Resource model** [Q1]. Is SCIM the right base, or something simpler?

8. **Standards venue** [Q6]. Internet2/InCommon, REFEDS, OIDF, IETF, OASIS, or Kantara.

## Getting involved

This is an early draft circulated for discussion, not a ratified standard. The most useful things a collaborator can do right now:

- **Read the spec and push back** — especially on the open items above.
- **Implement against it.** An independent implementation is the best stress test a protocol can get; the toolkit shows one Source System side to measure against.
- **Bring your context.** If your institution attests differently, that difference is signal worth capturing.

Open an issue or discussion to weigh in.

## Authors

- **David Hutchins** — University of Virginia
- **Kellen Murphy** — University of Virginia
- **Carter Griffin** — Northern Arizona University

With thanks to the identity community whose discussion shaped this draft; see the specification's acknowledgements.

## License

*To be finalized.* Suggested: the specification text under **CC BY 4.0** (free to share, quote, and implement) and the toolkit code under **Apache-2.0**. Confirm before publishing.
