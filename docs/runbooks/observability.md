# OpenRAG Observability Runbook

OpenRAG exports bounded, content-free traces, metrics, and redacted structured logs only when `OPENRAG_OTEL_ENDPOINT` is set. Prompts, responses, document text, filenames, memory, credentials, request bodies, and exception messages are excluded.

## Start the private stack

Create a strong Grafana password in the file referenced by `OPENRAG_GRAFANA_PASSWORD_FILE`, then start the profile:

```bash
OPENRAG_OTEL_ENDPOINT=http://otel-collector:4317 \
OPENRAG_GRAFANA_PASSWORD_FILE=/absolute/path/to/grafana_admin_password \
docker compose -f deploy/compose.yaml --profile observability up -d
```

Grafana is the only observability service exposed to the host, at `http://127.0.0.1:53000` by default. Prometheus, Loki, Tempo, and the OTLP collector remain on the internal `observability-network`.

## High response latency

Check the latency/TTFT panel, then correlate slow spans in Tempo with the same trace ID in Loki. Separate retrieval time from provider time in the OpenRAG superadmin operations page. Check model-provider health before increasing worker concurrency.

## High error rate

Open the grouped-error panel in OpenRAG, identify the safe error code and release, then inspect the correlated trace. Roll back a release if the rate began immediately after deployment. Never paste prompt or document content into incident notes.

## High no-answer rate

Confirm approved document versions are indexed, the workspace embedding profile matches the deployed vector collection, and retrieval thresholds have not drifted. Run the governed golden dataset before changing thresholds.

## Provider failures

Check LiteLLM health, provider quotas, model deployment state, timeouts, and circuit-breaker activity. Keep fallback models within the approved model catalog; do not bypass LiteLLM with direct provider calls.

## Queue age

Identify the affected queue label. Inspect worker health and lease recovery before scaling. Increase concurrency only within database, Redis, memory, and provider-rate limits. Evaluation work must remain isolated from interactive and ingestion queues.

## Event-loop lag

Look for synchronous parsing, blocking SDK calls, or CPU-bound work in the API process. Move such work to isolated workers. Do not compensate by only increasing API replicas.

## Database pool saturation

Check long transactions and database locks first. Confirm worker concurrency does not exceed the database connection budget. Increase the pool only after measuring database headroom.

## Retrieval quality

Compare retrieval recall, precision, MRR, and nDCG against the last approved evaluation run. Inspect document-version eligibility, hybrid search, reranking, and evidence span integrity. Do not lower confidence gates solely to improve answer rate.

## Citation quality

Check citation precision and recall on the sealed evaluation corpus. Confirm citations point to immutable document-version/evidence IDs and display document name, version, section, and page locators.

## Evaluation regression

Stop model, prompt, embedding, chunking, or reranker promotion when a governed quality gate fails. Compare candidate and baseline runs over the identical sealed corpus, investigate failed cases, and create a new immutable dataset version only when the approved evidence changes.

## Retention and recovery

Prometheus retains 30 days or 20 GB by default, Loki retains 30 days, and Tempo retains seven days. Override these limits only after capacity planning. Back up the named volumes using storage-consistent snapshots; restore into an isolated environment and validate datasource health before switching traffic.
