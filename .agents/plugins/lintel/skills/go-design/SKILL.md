---
name: go-design
description: Architectural and design patterns in Go
---
# Go Design

Architectural and design practices for Go projects under Lintel.

## 1. Contract Specification (`shared/phase-N/contract.md`)
- **Exact Package Paths & Exported Symbols:** Specify exact Go module/package import paths, exported struct definitions (field names, types, and struct tags where part of the contract), exported interfaces, and exported function/method signatures in `contract.md`. QA and DEV rely on `contract.md` as their single source of truth.
- **Context Propagation (`context.Context`):** Every function or method that performs I/O, network or persistence calls, blocking work, or cross-boundary orchestration must accept `ctx context.Context` as its first parameter. Never store `context.Context` inside a struct field.
- **Explicit Error Contracts (`errors.Is` / `errors.As`):** Declare all exported sentinel errors (`var ErrNotFound = errors.New(...)`) and custom structured error types (`type ValidationError struct`) in `contract.md`. Callers and QA tests must be able to inspect error conditions deterministically with `errors.Is` and `errors.As` rather than brittle string matching.
- **Zero-Value Usefulness & Configuration:** Design structs so their zero value is immediately usable where practical (such as `sync.Mutex` or `bytes.Buffer`). When initialization or validation is required, specify a `New<Type>(...)` constructor using either a clear `Config` struct or Functional Options (`type Option func(*options)`).
- **Deterministic Clock & Dependency Injection:** For time-sensitive, random, or external-boundary logic (token expiration, rate limiting, retry backoff, caching TTLs), design constructors or config structs that accept an injectable clock (`now func() time.Time` defaulting to `time.Now`) or small consumer interface so unit tests are 100% deterministic without `time.Sleep`.

## 2. Lintel Stub Files (`## Stub files` in `design.md`)
- **Compile Cleanly (`exit 1`, never `exit 2`):** Every stub created or updated by the Architect must compile cleanly under `go build ./...` and `go test -c` with zero syntax, import, or type-checking errors, exporting the exact packages, structs, interfaces, functions, methods, and sentinel errors declared in `contract.md`.
- **Fail Assertions at `lintel lock`:** Stub functions and methods must either `panic("not implemented")` or return a sentinel unimplemented error / zero-value placeholder that fails QA's assertions so `lintel lock` produces test failures (`exit 1`) and never build or compilation failures (`exit 2`).
- **Preserve Existing Exports:** When adding new types, methods, or functions to an existing package, never remove or break existing exported symbols—baseline tests for existing functionality must remain green during `lintel lock`.

## 3. Idiomatic Go Architecture & Package Organization
- **Standard Package Layout (`cmd/`, `internal/`, `pkg/`):**
  - `cmd/<binary>/`: Minimal `main` package responsible only for CLI/env parsing, configuration loading, signal handling, and dependency wiring; delegates execution immediately to packages under `internal/`.
  - `internal/<domain>/`: Private domain and application packages enforced by the Go toolchain. Organize packages by cohesive domain responsibility (e.g., `internal/billing`, `internal/auth`) rather than generic technical layers or catch-all utility packages (`models`, `utils`, `common`, `helpers`).
  - `pkg/<library>/`: Public reusable library packages only when external Go modules are explicitly intended to import them.
- **Small Consumer-Defined Interfaces ("Accept Interfaces, Return Structs"):** Keep interfaces small (typically 1–3 methods, like `io.Reader` or `io.Closer`) and define them in the consuming package where they are used rather than in the provider package. Return concrete structs from constructors so callers retain full type information while dependencies remain trivial to fake in unit tests.
- **Explicit Concurrency & Lifecycle Ownership:** Prefer synchronous functions by default and let callers add concurrency when needed. When a component manages goroutines or channels, explicitly document ownership, startup/shutdown lifecycle, cancellation via `ctx.Done()`, and synchronization invariants (`sync.Mutex`, `errgroup.Group`).

## 4. Test Outline Mapping (`## Test outline` in `design.md`)
- **`unit`:** Map pure domain functions, table-driven edge cases, boundary timestamps (using injected `now func() time.Time`), sentinel/typed error checks (`errors.Is`/`errors.As`), concurrency invariants, and consumer-side interface fakes to `unit`.
- **`integration`:** Map multi-package workflows, real persistence/filesystem/network adapters, and HTTP/gRPC server handler wiring (`net/http` handlers) to `integration`. If a phase only touches pure domain logic without an integration boundary, explicitly write `- integration: none: <clear justification>`.
- **`e2e`:** Map compiled binary executions (`cmd/`), full CLI invocations, or end-to-end service journeys to `e2e`. If a phase only adds or modifies an internal or library package with no CLI or service entrypoint changes, explicitly write `- e2e: none: <clear justification>`.

