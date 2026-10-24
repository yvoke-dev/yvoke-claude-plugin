# Operability Method Skill (`lintel:operability`)

The `operability` method skill provides stack-neutral production-readiness guidelines for Lintel personas (`Architect`, `DEV`, `Reviewer`, and `QA`). It ensures systems are designed, built, and verified to be observable, resilient to dependency faults, safe in resource consumption, and fail-fast on misconfiguration.

## What This Skill Covers

1. **Structured Logging**:
   - Machine-parseable key-value log records with correlation/request ID propagation.
   - Clear severity semantics (`ERROR`, `WARN`, `INFO`, `DEBUG`/`TRACE`).
   - Strict redaction of secrets, credentials, and personally identifiable information (PII).
2. **Metrics and Distributed Tracing**:
   - RED signals (Rate, Errors, Duration) for request-driven services and USE signals (Utilization, Saturation, Errors) for shared resources.
   - Latency histograms (`p50`, `p95`, `p99`), categorized error counters, bounded label cardinality, trace context propagation, and separated liveness/readiness probes.
3. **Resilience and Fault Tolerance**:
   - Explicit timeouts and deadline budgets on all external I/O.
   - Bounded retries on transient failures using exponential backoff and random jitter.
   - Circuit breaking, bulkhead isolation, graceful degradation, and idempotency for state-mutating operations.
4. **Resource Lifecycle Management**:
   - Deterministic cleanup of descriptors, sockets, streams, and pooled connections.
   - Bounded queues, memory protection, explicit backpressure, pagination limits, and graceful shutdown draining.
5. **Fail-Fast Configuration Validation**:
   - Startup validation of required configuration keys, types, and numeric bounds before serving traffic.
   - Conservative, secure defaults and actionable startup diagnostics.

## Usage in Lintel Workflows

- **During Architecture (`Architect`)**: Define operational requirements, timeout budgets, retry/idempotency contracts, capacity bounds, and telemetry signals in technical specifications.
- **During Implementation (`DEV`)**: Apply structured logging, metrics instrumentation, explicit I/O timeouts, deterministic resource cleanup, and startup configuration validation.
- **During Review & Verification (`Reviewer`, `QA`)**: Audit changes against the operability checklists and verify that fault handling, redaction, idempotency, and startup validation are exercised by automated tests.
