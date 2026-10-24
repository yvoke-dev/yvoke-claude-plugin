---
name: node-testing
description: Comprehensive testing standards and practices in Node.js and TypeScript (Vitest, Jest, Supertest, Playwright) for Lintel QA and QA-Reviewer
---
# Node.js & TypeScript Testing

Testing standards and practices for **QA** and **QA-Reviewer** in Node.js and TypeScript projects using Vitest, Jest, Supertest, and Playwright.

---

## 1. Lintel Gate & Exit Code Contract

Test runner scripts in `.lintel/scripts/test-<group>.sh` enforce the strict Lintel exit code contract across `unit`, `integration`, and `e2e` test groups:

- **`0` — Pass:** All executed tests in the group passed.
- **`1` — Assertion Failure:** The test runner started cleanly, executed the test suite, and at least one test assertion failed.
- **`2` — Build / Runtime / Configuration Error:** TypeScript compilation failed, an import could not be resolved, syntax was invalid, or the test environment crashed before assertions could run.
- **`3` — No Tests Found:** No test files exist for the target test group.

### Runner Execution Rules
- Always verify tests via `.lintel/scripts/test-<group>.sh` rather than invoking `npx vitest` or `npx jest` with ad-hoc flags that bypass Lintel's exit code translation.
- Treat any `exit 2` as a blocker: fix broken imports, type mismatches against `shared/phase-N/contract.md`, or missing test harness setup before attempting `lintel lock`.

---

## 2. Writing Tests for `lintel lock` (Red-on-Stub, Green-on-Spec)

During the QA phase, `lintel lock` validates your new tests against the Architect's unimplemented stubs before DEV writes the implementation.

### Core Invariants
1. **Import Only Contract Symbols:**
   - Import the exact module paths, functions, classes, constants, and TypeScript types declared in `shared/phase-N/contract.md`.
   - Do not invent helper exports or reach into internal implementation modules not listed in `contract.md`.
2. **Compile Cleanly (`no exit 2`):**
   - Tests must typecheck and import cleanly against the Architect's stub signatures.
   - Use accurate TypeScript types for all test inputs and expected outputs so `tsc --noEmit` and the test runner succeed without compilation errors.
3. **Fail with `exit 1` on Behavioral Assertions (Red-on-Stub):**
   - When executed against the Architect's stubs (which throw `new Error("Not implemented")` or return placeholder values), your test suite must fail with `exit 1` because behavioral assertions are not yet satisfied.
   - **Never assert on stub placeholders:** Never write `expect(() => fn()).toThrow("Not implemented")` or assert that a stub returns its unimplemented placeholder. A test that passes on the stub violates the red-on-stub gate and will fail once DEV implements the real behavior.
4. **Pass with `exit 0` on Spec Implementation (Green-on-Spec):**
   - Every assertion must verify the concrete behavior, return values, state transitions, and error conditions specified in `shared/requirements.md` and `shared/phase-N/contract.md`.
5. **Stay Strictly in the Test Lane:**
   - Place unit, integration, and E2E suites inside the designated test directories (`tests/`, `__tests__/`, `e2e/`).
   - Never modify production source files under `src/`.

---

## 3. Unit Testing Practices (Vitest & Jest)

### Requirement Traceability
- Structure `describe` blocks around contract symbols and requirement IDs (`R1`, `R2`, ...) and cover every scenario listed in `shared/phase-N/design.md`'s `## Test outline`.
- Name each `it` / `test` block with the input condition and expected outcome (e.g., `it("returns "just now" when elapsed time is under 60 seconds (R1)", ...)`).

### Table-Driven Tests (`describe.each` & `.each`) for Boundary Matrices
Use parameterized table-driven suites (`describe.each` or `it` / `test` with `.each`) or `for (const tc of cases)` loops to systematically verify boundary transitions, unit conversions, and equivalence partitions without repetitive boilerplate:

```ts
import { describe, expect, it } from "vitest";
import { formatDuration } from "../src/duration";

describe("formatDuration boundary matrix", () => {
  describe.each([
    { ms: 0, expected: "0s", label: "zero boundary" },
    { ms: 999, expected: "0s", label: "sub-second floor (t - 1)" },
    { ms: 1_000, expected: "1s", label: "exact 1-second threshold (t)" },
    { ms: 1_001, expected: "1s", label: "just above 1 second (t + 1)" },
    { ms: 59_999, expected: "59s", label: "upper second boundary" },
    { ms: 60_000, expected: "1m 0s", label: "exact minute rollover" },
    { ms: 3_600_000, expected: "1h 0m 0s", label: "exact hour rollover" },
  ])("$label ($ms ms)", ({ ms, expected }) => {
    it(`formats ${ms} ms as ${expected}`, () => {
      expect(formatDuration(ms)).toBe(expected);
    });
  });

  describe.each([
    { input: -1, reason: "negative number" },
    { input: Number.NaN, reason: "NaN" },
    { input: Number.POSITIVE_INFINITY, reason: "positive infinity" },
    { input: Number.NEGATIVE_INFINITY, reason: "negative infinity" },
  ])("invalid input: $reason", ({ input }) => {
    it("throws RangeError", () => {
      expect(() => formatDuration(input)).toThrow(RangeError);
    });
  });
});
```

### Deterministic Clock & Timer Testing
Never rely on wall-clock `Date.now()`, real `setTimeout` delays, or arbitrary `sleep()` calls in unit or integration tests:
- **Injectable Reference Timestamp (Preferred):** If `contract.md` exposes an optional `now` or `referenceTime` parameter, pass deterministic timestamps or `Date` instances directly in your test cases.
- **Fake Timers (`vi.useFakeTimers` / `vi.setSystemTime`):** When testing code that reads the system clock or schedules timers (`setTimeout`, `setInterval`, debounce/throttle):

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isTokenExpired } from "../src/token";

describe("isTokenExpired", () => {
  const FIXED_NOW = new Date("2026-01-15T12:00:00.000Z");

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(FIXED_NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns false 1ms before expiration", () => {
    const expiresAt = FIXED_NOW.getTime() + 1;
    expect(isTokenExpired(expiresAt)).toBe(false);
  });

  it("returns true at exact expiration timestamp", () => {
    const expiresAt = FIXED_NOW.getTime();
    expect(isTokenExpired(expiresAt)).toBe(true);
  });
});
```

### Mock & Spy Hygiene
- **Always Restore Mocks:** Register `afterEach(() => { vi.restoreAllMocks(); })` (or `jest.restoreAllMocks()`) in every suite that uses `vi.spyOn`, `vi.fn`, or stubbed globals so spies never leak across tests.
- **Prefer Real Collaborators Over Over-Mocking:** Mock only external boundaries (network clients, filesystem, clock, randomness). Never mock the unit under test or its pure internal data structures.
- **Fresh State Per Test:** Instantiate fresh fixtures inside `beforeEach` or factory helpers rather than mutating module-scoped objects shared across tests.

---

## 4. API & Integration Testing Practices (Supertest)

When testing HTTP handlers, Express/Fastify/Hono apps, or service boundaries in `tests/` integration suites:

```ts
import request from "supertest";
import { describe, expect, it } from "vitest";
import { createApp } from "../src/app";

describe("POST /api/items", () => {
  it("creates an item and returns 201 with JSON payload", async () => {
    const app = createApp();
    const response = await request(app)
      .post("/api/items")
      .send({ name: "Widget", quantity: 3 })
      .set("Accept", "application/json");

    expect(response.status).toBe(201);
    expect(response.headers["content-type"]).toMatch(/application\/json/);
    expect(response.body).toMatchObject({
      id: expect.any(String),
      name: "Widget",
      quantity: 3,
    });
  });

  it("returns 400 when payload violates contract validation", async () => {
    const app = createApp();
    const response = await request(app)
      .post("/api/items")
      .send({ name: "", quantity: -5 });

    expect(response.status).toBe(400);
    expect(response.body).toHaveProperty("error");
  });
});
```

- **Isolated App Instances:** Create a fresh application/container instance per test (or reset backing in-memory stores in `beforeEach`) so request state never bleeds between tests.
- **Verify Status, Headers, and Body:** Assert exact HTTP status codes (`200`, `201`, `400`, `401`, `404`, `409`, `422`), `Content-Type` headers, and structured error shapes defined in `contract.md`.
- **Ephemeral Ports Only:** Pass the Express/HTTP `app` directly to `request(app)` rather than binding to a hardcoded port that can collide in CI.

---

## 5. End-to-End Testing Practices (Playwright)

When authoring browser E2E suites under `e2e/` or `playwright/`:

### Accessible Role & Label Locators
Prioritize resilient, user-visible locators over brittle CSS class or DOM-hierarchy selectors:
1. `page.getByRole("button", { name: /save changes/i })`
2. `page.getByLabel("Email address")`
3. `page.getByPlaceholder("Search items...")`
4. `page.getByText("No results found")`
5. `page.getByTestId(...)` only when semantic role/label queries are not applicable.

### Web-First Auto-Retrying Assertions
Always `await` Playwright's web-first assertions so assertions automatically wait for UI state transitions and hydration without flaky `page.waitForTimeout(...)` sleeps:

```ts
import { expect, test } from "@playwright/test";

test("submits form and displays confirmation banner", async ({ page }) => {
  await page.route("**/api/ExternalAuth/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ authenticated: true, user: "alice" }),
    });
  });

  await page.goto("/");
  await page.getByLabel("Project name").fill("Lintel Release");
  await page.getByRole("button", { name: "Create project" }).click();

  await expect(page.getByRole("status")).toHaveText("Project Lintel Release created");
  await expect(page.getByRole("heading", { name: "Lintel Release" })).toBeVisible();
});
```

### Network Route Stubbing & Offline Determinism
- Intercept external third-party APIs, OAuth providers, or telemetry endpoints with `page.route(...)` and `route.fulfill(...)` so E2E tests run deterministically offline without external secrets.
- Test both happy-path responses and error/degraded states (e.g., `status: 500` or network abort) when required by `shared/requirements.md`.

### Worktree Cleanliness
- Never write untracked artifacts, screenshots, or temporary files into the repository root or `src/` during test execution.
- Ensure any diagnostic output remains strictly inside gitignored report directories (`test-results/`, `playwright-report/`, `e2e-report/`).

---

## 6. QA-Reviewer Verification Checklist

Before approving a QA test suite or running `lintel lock`, verify every item below:

- [ ] **Contract Alignment:** All imports, function signatures, option bags, return types, and error types match `shared/phase-N/contract.md` verbatim.
- [ ] **Exit Code `1` on Stubs:** Running `.lintel/scripts/test-<group>.sh` against the Architect's stubs exits with `1` (assertion failures), never `0` (false positive), `2` (compile/import error), or `3` (missing tests).
- [ ] **Zero Stub-Placeholder Assertions:** No test asserts `"Not implemented"` or checks for stub placeholder behavior.
- [ ] **Full Requirement & Boundary Coverage:** Every requirement in `shared/requirements.md` and every case in `shared/phase-N/design.md`'s `## Test outline` is covered, including `t - 1` / `t` / `t + 1` boundaries and invalid/edge inputs (`NaN`, `Infinity`, negative values, empty strings, missing fields).
- [ ] **Timer & Mock Isolation:** Fake timers are reset via `vi.useRealTimers()` and spies/mocks are restored via `vi.restoreAllMocks()` in `afterEach`.
- [ ] **Worktree Hygiene:** Only files inside `tests/`, `__tests__/`, or `e2e/` were created or modified, and no untracked artifacts pollute the repository.

