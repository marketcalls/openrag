## Inspiration

Organizations want to use generative AI with their internal knowledge, but most RAG demos stop where the difficult business requirements begin. Sensitive documents cannot always leave private infrastructure. Access permissions must still apply when information is retrieved. Answers need evidence. Administrators need control over models, costs, users, and data retention. Teams also need the freedom to adopt better models without rebuilding their knowledge platform around a new vendor.

OpenRAG was inspired by the idea that private AI should be deployable, understandable, and trustworthy. We set out to create an open, self-hosted platform that treats retrieval quality, security, citations, and operability as core product capabilities rather than later additions.

## What it does

OpenRAG is being built to turn private business documents into a secure, searchable knowledge system. The intended experience lets users upload or synchronize content, ask questions in natural language, and receive streamed answers grounded in sources they are authorized to access.

The completed product vision is designed to:

- Parse, chunk, embed, and index large document collections asynchronously.
- Combine dense and sparse search for stronger semantic and keyword retrieval.
- Generate answers with citations resolved to the stored source and page number.
- Isolate organizations and workspaces while enforcing document permissions inside retrieval.
- Work with hosted providers, local models, and OpenAI-compatible endpoints through one governed in-process LiteLLM runtime.
- Give administrators control over users, models, secrets, usage, audit history, and system health.
- Support Docker Compose first, followed by Kubernetes and fully air-gapped deployments in later phases.

Phase 1 focuses on authentication, workspaces, document ingestion, tenant-aware hybrid retrieval, streaming chat with citations, model registration, encrypted secrets, the audit write path, and core administration. Synchronization connectors, document-level ACLs, usage dashboards, Kubernetes packaging, and air-gapped bundles follow in later phases.

## How we built it

The approved OpenRAG architecture uses a modular monolith with independently scalable background workers. FastAPI provides the API, React and TypeScript power the web application, PostgreSQL stores relational data, MinIO stores source documents, Qdrant handles vector search, and Celery with Redis runs ingestion jobs. Agno performs bounded orchestration and the LiteLLM Python library invokes cloud and locally hosted models with request-scoped encrypted credentials.

In the planned flow, documents move through a staged ingestion pipeline: parsing, semantic chunking, dense and sparse embedding, and vector indexing. Phase 1 applies tenant and workspace filters as part of vector search, fuses retrieval results, and supplies the selected evidence to the language model as explicitly delimited data. Document-level permission filters will be added inside that same retrieval path during the ACL phase. Responses stream to the user with citations resolved back to stored sources.

Before implementation, we created a product requirements document, architecture decisions, security invariants, interface contracts, and task-level implementation plans. Development has now started with a test-first backend foundation so later ingestion, retrieval, chat, and frontend work can build on verified boundaries.

## Challenges we ran into

The hardest challenge was balancing an approachable first release with the requirements of a credible enterprise platform. Features such as document-level permissions, encrypted secrets, auditability, model portability, and tenant isolation affect the architecture from the first database table and retrieval query; they cannot be safely bolted on later.

Retrieval design introduced another trade-off. The preferred BGE-M3 model supports both dense and learned sparse representations, but the standard Text Embeddings Inference service exposes the dense path more readily. For the first phase, we chose TEI for dense embeddings and FastEmbed for BM25-family sparse vectors, fused natively in Qdrant. The retrieval interface keeps this implementation replaceable when a learned-sparse service is introduced.

We also had to design streaming chat as more than a sequence of text messages. Editing a previous question and regenerating an answer create branches, so we designed conversations as message trees with sibling navigation instead of flat lists.

## Accomplishments that we're proud of

We are proud of defining five implementation invariants that will protect the product as it grows:

1. Tenant isolation has one enforced query path for each data store.
2. Document permissions are applied inside vector retrieval.
3. Encrypted secrets have one controlled decryption path.
4. Authentication and authorization are declared at API boundaries.
5. Retrieved content and model output are always treated as untrusted data.

We also produced a phased design that preserves the long-term product vision while keeping the initial implementation achievable. The approved Phase 1 design includes hybrid retrieval, streamed citations, multi-tenant workspaces, encrypted model credentials, model-agnostic routing, an append-only audit write path, and adversarial isolation tests.

## What we learned

We learned that a dependable RAG product is primarily a systems problem. Model quality matters, but trust comes from the complete path around the model: ingestion reliability, permission-aware retrieval, evidence quality, honest no-answer behavior, secure secret handling, observability, and clear administration.

We also learned that vendor independence requires more than supporting several API keys. Model capabilities must be measured, routing must tolerate different provider behaviors, embedding choices must be isolated behind stable interfaces, and local operation must influence every dependency—from fonts to telemetry and model metadata.

Finally, planning security and test boundaries early makes the implementation easier to divide, review, and scale. Explicit interfaces let teams work independently without creating alternative code paths that bypass critical controls.

## What's next for OpenRAG

The immediate focus is implementing and verifying the Phase 1 foundation: the FastAPI service, authentication, organizations and workspaces, background ingestion, hybrid Qdrant retrieval, LiteLLM model registration, encrypted secrets, streaming chat with citations, and the initial React administration experience.

After that, OpenRAG will add document-level access controls, SSO, quotas, usage dashboards, model capability probing, and richer audit tooling. Later phases introduce agentic multi-step retrieval, reranking, chat attachments, exports, connectors, evaluation workflows, Kubernetes packaging, air-gapped bundles, and developer integrations.

The goal is to make OpenRAG a practical foundation for organizations that want powerful AI over private knowledge without surrendering control of their data, infrastructure, or model choices.
