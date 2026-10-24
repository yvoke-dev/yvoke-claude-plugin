---
name: go-implementation
description: Idiomatic Go implementation standards and engineering practices for DEV and DEV-Reviewer
---
# Go Implementation

Coding standards, idiomatic patterns, and verification practices for DEV and DEV-Reviewer in Go projects.

## 1. Contract & Lane Discipline
- **Exact Contract Adherence:** Implement the exact package paths, file locations, exported types, struct fields, struct tags (`json`, `yaml`, `db`), function/method signatures, receiver types (value vs pointer), and error semantics defined in `shared/phase-N/contract.md`.
- **Strict Lane Separation:** DEV may only create or modify production Go source files. Never edit, create, or delete test files or directories (`tests/`, `e2e/`, `testdata/`) or Lintel scripts (`.lintel/scripts/`). Never read QA's locked test files during implementation—implement strictly from `requirements.md`, `design.md`, and `contract.md`.
- **Formatting & Static Analysis:** All code must be formatted with `gofmt` / `goimports` and pass `go vet` with zero warnings. Exported identifiers should have concise doc comments starting with the identifier name.

## 2. Error Handling
- **Never Ignore Errors:** Always check returned `error` values immediately. Never assign an error to `_` unless explicitly justified by the contract (e.g., best-effort cleanup after a primary error is already captured).
- **Error Wrapping & Context:** Wrap lower-level errors with actionable context and the `%w` verb (`fmt.Errorf("load user %s: %w", id, err)`) so callers can inspect the error chain via `errors.Is` and `errors.As`. Use `%v` only when intentionally hiding internal implementation details at a public boundary.
- **Sentinel & Typed Errors:** Define and return the exact sentinel errors (`var ErrNotFound = errors.New(...)`) or custom error types (`type ValidationError struct { ... }`) specified in `contract.md`. Compare errors using `errors.Is(err, target)` and extract typed errors using `errors.As(err, &target)` rather than direct `==` or string matching.
- **No Panics in Normal Control Flow:** Reserve `panic` strictly for truly unreachable programmer invariants during startup initialization (`Must*` helpers). Production request handlers, parsers, and library functions must return `error` values.

## 3. Context Propagation & Cancellation
- **First Parameter Convention:** Accept `ctx context.Context` as the first parameter on all I/O-bound, blocking, network, database, or cancellable functions as specified in `contract.md`. Never store a `context.Context` inside a struct; pass it explicitly through the call stack.
- **Respect Cancellation & Deadlines:** Check `ctx.Err()` or `select` on `<-ctx.Done()` in long-running loops, retry backoff waits, and streaming pipelines. Return `ctx.Err()` promptly when canceled.
- **Outbound Timeouts:** Always pass request-scoped contexts (`http.NewRequestWithContext`, `db.QueryContext`, `exec.CommandContext`) to outbound HTTP, database, and subprocess calls. Ensure `http.Client`, `http.Server`, and database connection pools configure explicit timeouts (`Timeout`, `ReadHeaderTimeout`, `IdleTimeout`) rather than relying on unbounded defaults.
- **Cancel Functions:** Whenever creating a derived context with `context.WithCancel`, `context.WithTimeout`, or `context.WithDeadline`, immediately `defer cancel()` to release timer and context resources.

## 4. Concurrency Safety & Goroutine Lifecycle
- **Bounded Lifecycles (No Goroutine Leaks):** Every spawned goroutine must have a deterministic exit condition tied to `ctx.Done()`, a closed channel, or an explicit shutdown signal, and must be awaited before the owning function or component returns (via `sync.WaitGroup` or `golang.org/x/sync/errgroup`).
- **Channel Discipline:** The sender (producer) owns closing a channel; receivers must never close a channel. Avoid unbuffered channel sends that can block forever if the receiver exits early on error—use `select` with `<-ctx.Done()` or appropriately buffered channels.
- **Synchronization Primitives:** Protect shared mutable state with `sync.Mutex` or `sync.RWMutex`. Keep critical sections small, `defer mu.Unlock()` immediately after locking (or unlock explicitly before blocking I/O), and never copy structs containing `sync.Mutex`, `sync.WaitGroup`, `sync.Once`, or `atomic` values after first use.
- **Race-Detector Cleanliness:** All concurrent code must pass `go test -race` without data races. Do not rely on benign data races or unsynchronized map/slice access across goroutines.

## 5. Resource Cleanup & Memory Semantics
- **Deterministic Cleanup (`defer`):** Always close files, network connections, HTTP response bodies (`resp.Body.Close()`), database rows (`rows.Close()`), and transactions (`defer tx.Rollback()`) using `defer` immediately after verifying the acquisition error is `nil`. Do not `defer` inside unbounded loops; extract a helper function so deferred cleanup runs per iteration.
- **HTTP Response Bodies & `sql.Rows`:** Drain and close `resp.Body` so underlying TCP connections can be reused, and always check `rows.Err()` after iterating over `sql.Rows`.
- **Slices & Maps:**
  - **Nil vs Empty Slices:** Respect `contract.md` JSON serialization expectations—a `nil` slice marshals to `null` in `encoding/json`, whereas an initialized empty slice (`[]T{}` or `make([]T, 0)`) marshals to `[]`.
  - **Preallocation:** Preallocate slices (`make([]T, 0, len(src))`) and maps (`make(map[K]V, len(src))`) when the target capacity is known to minimize allocations.
  - **Safe Map Access & Mutation:** Never write to a `nil` map. Guard against aliasing when returning internal slices or maps to callers by returning defensive copies (`slices.Clone`, `maps.Clone`) when immutability is required.

## 6. Structured Logging & Security (`log/slog`)
- **Structured Logging:** Use the standard library `log/slog` package (or the project's configured logger) with structured key-value attributes (`slog.String`, `slog.Int`, `slog.Any`). Do not leave debug `fmt.Println` or `log.Printf` statements in production code unless writing to an explicit CLI `io.Writer` required by the contract.
- **Secret & PII Redaction:** Never hardcode credentials, API keys, or tokens, and never log raw secrets, passwords, authorization headers, session cookies, or sensitive personal data. Implement `slog.LogValuer` on sensitive domain types to redact values automatically.

## 7. Gate Verification Before `done`
1. Run `./.lintel/scripts/build.sh` to verify that `go build`, `go vet`, and project static checks compile cleanly.
2. Run `./.lintel/bin/lintel check --phase <N>` to execute the full test gate against your candidate tree in an isolated worktree.
3. Only call `./.lintel/bin/lintel done --verdict complete` (or `approve` for DEV-Reviewer) after `lintel check --phase <N>` records a green result for your current working tree.

