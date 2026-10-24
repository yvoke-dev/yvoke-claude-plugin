---
name: operability
description: Stack-neutral practices for production readiness, observability, structured logging, metrics, distributed tracing, resilience, resource lifecycle management, and startup configuration validation.
---

# Operability and Production Readiness Method Skill

This skill defines stack-neutral engineering standards for designing, implementing, and reviewing operable, observable, and resilient software systems. Apply these practices during architecture specification (`Architect`), implementation (`DEV`), and verification/review (`Reviewer`, `QA`).

---

## 1. Structured Logging

Logs must be machine-parseable, context-rich, and safe for centralized ingestion.

### 1.1 Format and Context Propagation
- **Structured Output**: Emit logs as structured key-value records (such as JSON) rather than unstructured free-form text strings. Keep the event message static and place variable data in dedicated structured fields.
- **Correlation and Request IDs**: Every inbound request, background job, or asynchronous message must be assigned or propagate a `correlation_id` (or `trace_id` / `request_id`). Include this identifier in every log entry emitted along the execution path.
- **Standard Contextual Fields**: Include consistent metadata fields across services: timestamp (ISO-8601 UTC), severity level, service/component name, operation name, correlation identifier, and error code/category when applicable.

### 1.2 Log Levels and Semantics
- **`ERROR`**: Actionable failures where an operation could not complete and requires investigation or alerting (e.g., unhandled exceptions, exhausted retries to a critical dependency, data corruption).
- **`WARN`**: Recoverable anomalies or degraded states where the request succeeded or was safely deferred, but the condition warrants tracking (e.g., fallback activated, retry succeeded after transient failure, approaching rate or capacity thresholds).
- **`INFO`**: High-value lifecycle milestones and state transitions (e.g., service startup/shutdown, configuration loaded, batch job completion summary). Avoid per-request `INFO` spam on high-throughput paths unless explicitly sampled.
- **`DEBUG` / `TRACE`**: Detailed diagnostic state for local troubleshooting or targeted dynamic sampling. Must be disabled by default in production environments.

### 1.3 Sensitive Data Redaction
- **Zero Secrets in Logs**: Never log credentials, API keys, session tokens, private keys, authorization headers, or connection strings containing passwords.
- **PII Protection**: Redact, mask, or hash personally identifiable information (PII), financial data, and regulated health/user attributes before serialization into log records.
- **Safe Error Serialization**: Ensure exception formatters and request-dump utilities scrub raw payloads and headers before writing failure context.

---

## 2. Metrics and Distributed Tracing

Telemetry must allow operators to detect symptoms immediately and isolate root causes across component boundaries without deploying ad-hoc code changes.

### 2.1 Core Signal Methodologies (RED and USE)
- **RED Signals for Request-Driven Components**:
  - **Rate**: Requests or operations processed per second.
  - **Errors**: Failed requests per second, segmented by error class (client fault vs. server fault vs. dependency fault).
  - **Duration**: Distribution of execution latency.
- **USE Signals for Shared Resources (Pools, Queues, Storage, Compute)**:
  - **Utilization**: Percentage of time or capacity the resource is busy.
  - **Saturation**: Degree of queued work waiting for the resource (e.g., connection pool wait queue length, thread/worker backlog).
  - **Errors**: Count of resource allocation or hardware/transport failures.

### 2.2 Metric Types and Cardinality Discipline
- **Latency Histograms**: Measure operation and dependency durations using histograms or summaries that expose tail percentiles (`p50`, `p95`, `p99`) rather than simple averages.
- **Monotonic Counters**: Track cumulative totals for requests, retries, timeouts, circuit-breaker state transitions, and categorized failures.
- **Gauges**: Track point-in-time measurements such as active connections, queue depth, and in-flight requests.
- **Bounded Label Cardinality**: Never use unbounded values (user IDs, request IDs, raw URLs, email addresses, timestamps) as metric labels or dimensions. High-cardinality identifiers belong in logs and trace attributes.

### 2.3 Distributed Tracing and Health Probes
- **Context Propagation**: Propagate trace context headers across all synchronous RPC/HTTP calls and asynchronous message broker envelopes.
- **Span Boundaries**: Create spans around external I/O calls, database queries, and significant internal processing stages, annotating spans with status codes and retry counts.
- **Liveness vs. Readiness Probes**:
  - **Liveness**: Indicates whether the process is deadlocked or unrecoverably broken and requires restart. Must not depend on external downstream dependencies.
  - **Readiness**: Indicates whether the instance is initialized and capable of serving traffic. Should reflect critical local readiness and graceful drain state.

---

## 3. Resilience and Fault Tolerance

Every external dependency (network services, databases, caches, file systems, third-party APIs) will eventually slow down or fail. Systems must isolate faults and prevent cascading outages.

### 3.1 Explicit Timeouts on All External I/O
- **No Unbounded Waits**: Every network socket, HTTP client, RPC stub, database query, and lock acquisition must configure explicit connect, read, and overall deadline timeouts.
- **Deadline Budgets**: Downstream timeouts must be strictly shorter than the caller's remaining request budget so callers do not abandon requests while downstream work continues consuming resources.

### 3.2 Bounded Retries with Exponential Backoff and Jitter
- **Cap Retry Attempts**: Never retry indefinitely. Configure an explicit maximum retry count or total retry time budget.
- **Retry Only Transient Failures**: Restrict retries to transient transport errors, timeouts, or explicit rate-limit/unavailable responses. Never retry deterministic client errors (such as validation failures or unauthorized requests).
- **Exponential Backoff with Jitter**: Increase delay exponentially between attempts and inject random jitter to prevent synchronized retry storms ("thundering herds") against recovering dependencies.

### 3.3 Circuit Breaking and Bulkheads
- **Circuit Breakers**: Wrap critical remote dependencies with circuit breakers that trip open when failure or slow-call rates exceed a threshold, failing fast while the dependency recovers and probing periodically in a half-open state.
- **Bulkhead Isolation**: Partition connection pools, worker pools, and queues per downstream dependency or workload priority so a slow dependency cannot exhaust shared capacity for unrelated operations.
- **Graceful Degradation**: Define explicit fallback behavior when non-critical dependencies fail (e.g., returning cached data, serving partial responses, or queuing work for asynchronous reconciliation).

### 3.4 Idempotency
- **Safe Retries for State Mutations**: Any mutating operation that can be retried by a client, message consumer, or workflow engine must be idempotent.
- **Idempotency Keys and Conditional Updates**: Use client-supplied idempotency keys, deterministic deduplication records, or optimistic concurrency control (version checks / conditional writes) to ensure repeated execution produces the exact same system state without duplicate side effects.

---

## 4. Resource Lifecycle Management

Unbounded resource consumption leads to memory exhaustion, descriptor leaks, and sudden process termination under load.

### 4.1 Deterministic Cleanup of Descriptors and Connections
- **Scoped Acquisition and Release**: Always release file handles, network sockets, database connections, cursors, and stream buffers in deterministic cleanup blocks (`finally`, scoped resource guards, or `defer`/disposal patterns) even when exceptions occur.
- **Connection Pooling**: Reuse expensive network and database connections via bounded pools with configured maximum size, idle eviction timeouts, and connection lifetime limits.

### 4.2 Bounded Queues and Memory Protection
- **No Unbounded In-Memory Queues**: Every internal work queue, channel, batch buffer, and in-memory cache must enforce a strict maximum capacity.
- **Explicit Backpressure**: Define clear behavior when a queue or buffer reaches capacity (e.g., block with timeout, shed load with an explicit capacity error, or drop oldest low-priority telemetry).
- **Pagination and Streaming**: Enforce maximum page sizes on queries and API responses; stream or chunk large payloads instead of loading unbounded datasets into memory.

### 4.3 Graceful Shutdown
- **Signal Handling and Drain**: On termination signals, immediately mark readiness probes as unready, stop accepting new inbound requests, allow in-flight operations to complete within a bounded drain period, and flush telemetry buffers and open connections before exiting.

---

## 5. Fail-Fast Configuration Validation at Startup

Invalid or missing configuration must be caught immediately at process initialization rather than causing latent runtime failures when a code path is first exercised in production.

### 5.1 Startup Schema and Constraint Verification
- **Validate Before Serving Traffic**: Parse and validate all required environment variables, configuration files, and feature flags during bootstrap before binding traffic listeners or marking readiness healthy.
- **Typed and Bounded Checks**: Verify data types, numeric ranges (e.g., positive timeout values, valid port numbers, non-zero pool sizes), URL formats, and enum values.
- **Actionable Startup Errors**: When validation fails, emit a clear structured diagnostic naming the missing or invalid configuration keys (without echoing secret values) and terminate initialization immediately with a non-zero exit status.

### 5.2 Safe Defaults and Environment Separation
- **Explicit Environment Overrides**: Externalize environment-specific settings (endpoints, timeouts, pool capacities, credentials) from code.
- **Secure and Conservative Defaults**: Never default to insecure modes (such as disabled TLS verification, wildcard CORS, or debug logging) when configuration keys are omitted. Require explicit opt-in for development-only bypasses.

---

## 6. Role Checklists

### For `Architect` (Specifications & Design)
- [ ] Are SLIs/SLOs, RED/USE metrics, and key failure modes defined for new components and external integrations?
- [ ] Are timeout budgets, retry policies, circuit-breaker boundaries, and fallback behaviors specified for every remote dependency?
- [ ] Are mutating APIs and asynchronous consumers designed with explicit idempotency mechanisms?
- [ ] Are queue capacities, pagination limits, and backpressure strategies bounded in the design?
- [ ] Are all required configuration parameters and their validation rules documented?

### For `DEV` (Implementation)
- [ ] Do all log statements use structured fields, propagate correlation/request IDs, use appropriate severity levels, and redact secrets/PII?
- [ ] Are latency histograms, error counters, and saturation gauges emitted with bounded label cardinality?
- [ ] Does every external I/O call specify an explicit timeout and, where appropriate, bounded retries with exponential backoff and jitter?
- [ ] Are all file descriptors, streams, and connections deterministically closed on both success and failure paths?
- [ ] Does application startup validate all required configuration and fail fast on invalid or missing settings?

### For Reviewers (`Reviewer`, `QA`)
- [ ] Verify no external HTTP, RPC, or database call lacks a timeout or uses unbounded retries.
- [ ] Verify no sensitive fields (passwords, tokens, PII) appear in log messages, exception dumps, or metric labels.
- [ ] Verify all queues, caches, and batch operations have upper bounds and tested backpressure/error handling.
- [ ] Verify failure paths (timeouts, dependency errors, invalid startup configuration, duplicate requests) are covered by automated verification tests.
