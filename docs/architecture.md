# OpenRAG architecture

OpenRAG is a modular monolith deployed as separate API and worker processes. Business rules live in domain modules shared by both processes; PostgreSQL, Redis, MinIO, Qdrant, LiteLLM, and the embedding service remain replaceable infrastructure boundaries.

```mermaid
flowchart TB
    subgraph Clients[Clients]
        Web[React web app]
        APIClient[REST / SDK clients]
    end

    subgraph App[OpenRAG application]
        API[FastAPI service\nAuth · tenancy · chat · admin]
        Worker[Celery workers\nParse · chunk · embed · delete]
        Retrieval[Retrieval module\nTenant filters · dense + sparse fusion]
    end

    subgraph Data[State and content]
        PG[(PostgreSQL\nmetadata · users · chats · secrets)]
        Redis[(Redis\nqueue · rate limits)]
        MinIO[(MinIO / S3\noriginal documents)]
        Qdrant[(Qdrant\nvectors · sparse terms)]
    end

    subgraph Intelligence[Document and model services]
        Docling[Docling\nlayout-aware parsing]
        TEI[TEI / BGE-M3\nembeddings]
        LiteLLM[LiteLLM proxy\nhosted + local models]
    end

    Web -->|HTTPS / SSE| API
    APIClient -->|REST / SSE| API
    API --> PG
    API --> Redis
    API --> MinIO
    API --> Retrieval
    API --> LiteLLM
    Redis -->|Celery jobs| Worker
    Worker --> Docling
    Worker --> TEI
    Worker --> MinIO
    Worker --> PG
    Worker --> Qdrant
    Retrieval --> Qdrant
    Retrieval --> TEI
```

## Ingestion flow

1. The API authorizes the user and workspace, stores the original file in MinIO, creates document metadata in PostgreSQL, and enqueues a Celery chain.
2. A worker parses supported documents with Docling, preserving page and table context.
3. The worker creates searchable chunks, generates dense and sparse representations, and upserts them to Qdrant with organization and workspace identifiers.
4. Document status moves through queued, processing, indexed, or failed; the frontend polls active documents and displays the transition without a reload.
5. Deletion is asynchronous and propagates to PostgreSQL metadata, object storage, and Qdrant.

## Query flow

1. The API authenticates the user and resolves their organization and selected workspace.
2. The retrieval module constructs the single tenant-aware Qdrant filter path, performs dense and sparse search, and fuses ranked results.
3. Relevant chunks are sent through LiteLLM to the selected hosted or local completion model.
4. The API streams retrieval events, answer deltas, citations, usage, and completion status over SSE.
5. The frontend renders sanitized Markdown, citation chips, source metadata, and branch-aware message controls.

## Security boundaries

- Organization and workspace identifiers are enforced inside storage and retrieval queries, not filtered after results return.
- Provider keys are write-only API inputs, envelope-encrypted in PostgreSQL, and exposed later only as fingerprints.
- Access tokens stay in frontend memory; refresh tokens use HTTP-only cookies.
- Document text and model output are treated as untrusted content.
- API authorization uses declared role dependencies, and superadmin-only model operations are independently protected from the UI.

## Current deployment shape

The development stack exposes the React app and FastAPI service directly. A production deployment should put them behind TLS ingress/load balancing and add OpenTelemetry, Prometheus, and Grafana. OCR, external connectors, Kubernetes/Helm packaging, public CLI/SDK packages, and high-availability topology are roadmap items rather than hidden assumptions in the current stack.
