---
name: go-testing
description: Testing standards and practices in Go for Lintel QA and QA-Reviewer
---
# Go Testing

Testing standards and practices for QA and QA-Reviewer in Go projects using the standard library `testing` package.

## 1. Lintel Gate & Exit Code Contract

Test scripts in `.lintel/scripts/test-<group>.sh` enforce the Lintel exit code contract (`0`/`1`/`2`/`3`):
- `0`: All tests in the group passed.
- `1`: Tests compiled and ran, and at least one test assertion or unhandled panic failed.
- `2`: Compilation error, missing import, syntax error, or environment failure prevented tests from running.
- `3`: No test sources exist for the group.

## 2. Writing Tests for `lintel lock` (Red-on-Stub, Green-on-Spec)

- **Reference Exact Contract Symbols:** Import and call only the packages, exported types, functions, methods, and sentinel errors declared in `shared/phase-N/contract.md`.
- **Red-on-Stub (`exit 1`) at `lintel lock`:** During `lintel lock`, Lintel runs the test suite against the Architect's unimplemented stubs. Tests must compile cleanly without type or import errors (never `exit 2`) and fail with `exit 1` on behavioral assertions or unrecovered stub panics.
- **Green-on-Spec Assertions:** Never write tests that assert a stub panics with `"not implemented"` or returns placeholder stub values. Always assert the real expected outputs, state transitions, and error conditions defined in `shared/requirements.md` and `shared/phase-N/contract.md` so the locked test suite passes (`exit 0`) once DEV implements the specification.
- **Stay in the Test Lane:** QA may only create or modify files classified as `tests` (and test dependencies in `go.mod` / `go.sum` when required). Never modify production implementation files. Refer to test packages or test directories by package or directory name rather than literal test filenames.

## 3. Table-Driven Subtests & Test Structure

- **Table-Driven Subtests (`t.Run`):** Structure unit and integration tests using table-driven slices of test cases iterated with `t.Run(tc.name, ...)`:
  ```go
  func TestParseDuration(t *testing.T) {
      cases := []struct {
          name    string
          input   string
          want    int64
          wantErr error
      }{
          {name: "valid_seconds", input: "10s", want: 10},
          {name: "zero_value", input: "0s", want: 0},
          {name: "empty_input", input: "", wantErr: ErrInvalidInput},
      }
      for _, tc := range cases {
          t.Run(tc.name, func(t *testing.T) {
              got, err := ParseDuration(tc.input)
              if tc.wantErr != nil {
                  if !errors.Is(err, tc.wantErr) {
                      t.Fatalf("ParseDuration(%q) error = %v, want %v", tc.input, err, tc.wantErr)
                  }
                  return
              }
              if err != nil {
                  t.Fatalf("ParseDuration(%q) unexpected error: %v", tc.input, err)
              }
              if got != tc.want {
                  t.Errorf("ParseDuration(%q) = %v, want %v", tc.input, got, tc.want)
              }
          })
      }
  }
  ```
- **Traceability & Boundary Coverage:** Cover every requirement (`R1`, `R2`, ...) and every scenario in `design.md`'s `## Test outline`, including exact boundary values (`n-1`, `n`, `n+1`, zero values, `nil` slices/maps, empty strings, and context cancellation).
- **Helper Functions (`t.Helper()`):** Mark every test assertion or setup helper with `t.Helper()` as its first statement so failure line numbers point to the failing subtest call site rather than inside the helper.

## 4. Resource Lifecycle & Clean Worktree Guarantee

- **Temporary Directories (`t.TempDir()`):** Always use `t.TempDir()` for filesystem fixtures, SQLite databases, sockets, or config files. Never write files into the repository working tree or relative paths—Lintel gate worktrees must remain 100% clean after test execution.
- **Deterministic Teardown (`t.Cleanup()`):** Register resource teardown (closing servers, cancelling contexts, resetting environment state, closing connections) with `t.Cleanup(func() { ... })` immediately after allocation so cleanup runs reliably even when a test calls `t.Fatal` or panics.

## 5. Error Assertions (`errors.Is` and `errors.As`)

- **Semantic Error Matching:** Never compare `err.Error()` strings unless the exact message format is explicitly mandated by `contract.md`.
- **Sentinel Errors (`errors.Is`):** Use `errors.Is(err, ExpectedSentinel)` to verify wrapped sentinel errors (such as `context.Canceled`, `sql.ErrNoRows`, or domain errors from `contract.md`).
- **Structured Error Types (`errors.As`):** Use `errors.As(err, &targetErr)` to inspect typed error fields, status codes, or validation details.

## 6. Deterministic HTTP & Integration Testing

- **Local Loopback Servers (`net/http/httptest`):** Use `NewServer`, `NewTLSServer`, or `NewRecorder` from `net/http/httptest` for testing HTTP clients, handlers, webhooks, and middleware deterministically offline without external network calls or hardcoded ports—specifically via httptest.NewServer.
  ```go
  // Spin up an isolated loopback server via httptest.NewServer.
  srv := NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
      w.Header().Set("Content-Type", "application/json")
      w.WriteHeader(http.StatusOK)
      _, _ = w.Write([]byte(`{"status":"ok"}`))
  }))
  t.Cleanup(srv.Close)
  ```
- **No Flaky Sleeps:** Avoid `time.Sleep` for synchronization; coordinate goroutines with channels, `context.WithTimeout`, or injectable clocks defined in `contract.md`.

## 7. Execution Flags: Race Detection (`-race`) & Uncached Runs (`-count=1`)

- **Disable Test Caching (`-count=1`):** Always pass `-count=1` to `go test` in gate scripts and verification runs so Go's build cache never masks non-deterministic failures or stale results.
- **Race Detector (`-race`):** Run `go test -race -count=1 ./...` to detect concurrent map/memory data races across goroutines before approving QA or DEV gates.

## 8. QA-Reviewer Checklist

When reviewing tests before `lintel lock`, verify:
1. Every requirement in `shared/requirements.md` and every item in `shared/phase-N/contract.md` has corresponding subtests.
2. Tests compile cleanly and fail with `exit 1` against the Architect's stubs (no assertions expecting `"not implemented"` panics).
3. Helpers call `t.Helper()`, teardown uses `t.Cleanup()`, and all filesystem operations use `t.TempDir()`.
4. Error checks use `errors.Is` / `errors.As` and HTTP tests use `net/http/httptest` via httptest.NewServer.
5. No untracked files or dirty state remain in the worktree after running `go test -race -count=1`.
