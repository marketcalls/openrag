# Branch-Safe Follow-up Grounding Design

## Problem

OpenRAG can answer an initial document question with grounded citations and then refuse a short follow-up such as `extract structure info`. The current retrieval pass embeds that short turn by itself. Conversation history reaches the answer prompt, but it does not rescue retrieval when the isolated follow-up scores weakly. The result is a false `below_threshold` refusal even though the immediately preceding answer already established the relevant evidence.

RAGHub solves this class of failure with citation backfill: when fresh retrieval is weak or under-filled, it rehydrates evidence cited by the nearest assistant ancestor on the active branch. OpenRAG will adopt that behavior while preserving its stricter document-authority rules.

## Decision

Carry forward citations from only the nearest previous assistant message on the active conversation branch. Do not scan through multiple older assistant turns. If the nearest assistant response has no citations, no backfill occurs.

This choice is:

- branch-safe for edited and regenerated conversations;
- topic-local, because it cannot silently reach past an intervening refusal or topic change;
- token-efficient, because it reuses at most one answer's bounded citation set;
- deterministic and auditable, with no extra model call required.

## Retrieval Flow

1. Route the current turn normally. Greetings and explicit conversation-history questions continue to bypass document retrieval.
2. Run fresh tenant-scoped hybrid retrieval using the workspace's configured `top_k` and confidence policy.
3. Run any existing bounded agent evidence-gathering escalation.
4. If the final fresh result is a no-answer or contains fewer than `top_k` chunks, load citations from the nearest assistant ancestor on the active branch.
5. Rehydrate those citation identities through a dedicated retrieval boundary:
   - Authority citations are reconstructed only after PostgreSQL revalidates organization, workspace, current approved version, provenance readiness, effective/expiry dates, source presence, ACL policy, and content hash.
   - Legacy citation chunk references are parsed defensively and fetched from Qdrant only under organization, workspace, and document filters. Their document IDs are then checked against the same current SQL eligibility used by legacy retrieval.
6. Merge fresh and backfilled evidence in priority order, deduplicate by stable evidence identity, and cap the result at `top_k`.
7. A surviving authorized backfill is sufficient grounding even when the isolated follow-up has low similarity. It is marked with score `0.0` so the UI and audit trail never misrepresent it as a fresh similarity hit.
8. Build the normal grounded prompt, stream the answer, validate citations, and persist the answer through the existing strict release gate.

## Component Boundaries

### Chat service

The chat service owns branch traversal and chooses the nearest assistant ancestor. It passes immutable citation identities to a `CitationBackfiller` protocol, merges the returned `RetrievalResult`, and records the final retrieval context. This keeps conversation-tree knowledge out of the retrieval module.

### Retrieval service

The retrieval service owns all Qdrant filters and current-authority checks. A new citation-rehydration function accepts bounded citation identities and returns normal `RetrievalResult` objects. No document text is trusted directly from the historic chat message or citation snapshot.

### Runtime wiring

Both direct API streaming and durable run workers receive the same production backfiller. Tests may inject a deterministic fake through the same protocol. There is no provider-specific or OpenAI-direct path.

## Error and Security Behavior

- Malformed, duplicate, cross-tenant, deleted, obsolete, superseded, expired, unapproved, or content-hash-mismatched references are silently discarded.
- Backfill never broadens tenant or workspace scope and never bypasses document authority.
- Failure to recover valid prior evidence leaves the existing no-answer response unchanged.
- Backfill is bounded by `top_k`, the prior citation count, and finite Qdrant scroll limits.
- Historical citation metadata is an identity hint, not trusted evidence content.

## Tests

Automated coverage must prove:

- a weak `extract structure info` follow-up reuses the nearest grounded answer's citations and does not refuse;
- the nearest assistant with no citations prevents reaching further back;
- edited branches use only their own ancestor citations;
- malformed and cross-tenant legacy references are rejected;
- authority references are revalidated and stale versions are rejected;
- fresh evidence outranks backfilled evidence and duplicates are removed;
- the final source list never exceeds `top_k`;
- direct greetings still avoid retrieval;
- `prev` conversation-history wording routes to the conversation path rather than RAG.

## Acceptance Criteria

In the live invoice thread, asking `extract structure info` immediately after a grounded invoice answer produces a streamed, cited answer instead of the confidence-threshold refusal. The response cites only currently accessible evidence, and the run ledger reports a grounded outcome with non-zero citation count.
