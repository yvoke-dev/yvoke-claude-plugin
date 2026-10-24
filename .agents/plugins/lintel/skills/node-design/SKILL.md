---
name: node-design
description: Architectural and design patterns for Node.js and TypeScript projects under Lintel
---
# Node.js & TypeScript Architectural Design

Architectural and design guidelines for Lintel's **Architect** and **Architect-Reviewer** when designing Node.js and TypeScript systems, authoring `shared/phase-N/design.md` and `shared/phase-N/contract.md`, and creating TypeScript stub files.

---

## 1. Type-First Contracts (`shared/phase-N/contract.md`)

`contract.md` is the binding agreement between the **Architect**, **QA**, and **DEV**. Because QA writes tests against `contract.md` and the stub files before DEV writes the implementation, every exported symbol and type signature must be unambiguous and self-contained.

### 1.1 Explicit Exported Signatures
- Declare the exact module import path (e.g., `src/lib/token.ts` or `@/lib/token`), exported symbol names, generic type parameters, parameter names and types, and return types.
- Distinguish **value exports** (`export function`, `export class`, `export const`) from **type-only exports** (`export type`, `export interface`) so `verbatimModuleSyntax` and `isolatedModules` builds never fail.

### 1.2 Discriminated Unions & Branded / Readonly Value Types
- **Make illegal states unrepresentable:** Replace boolean flag combinations and optional properties that covary (`{ loading: boolean; data?: T; error?: string }`) with explicit **discriminated unions** keyed on a literal discriminant tag (`status`, `kind`, or `ok`):
  ```ts
  export type ParseResult<T> =
    | { readonly ok: true; readonly value: T }
    | { readonly ok: false; readonly code: "INVALID_SYNTAX" | "EXPIRED"; readonly message: string };
  ```
- **Immutability by Default:** Mark data-transfer properties, arrays, and collection structures as `readonly` (`readonly items: readonly Item[]`, `ReadonlyMap<K, V>`) unless in-place mutation is an explicit part of the contract.
- **Branded / Nominal Types for Domain Identifiers:** Where raw primitives (`string`, `number`) risk accidental transposition (e.g., `userId` vs `organizationId`, or `Milliseconds` vs `Seconds`), define branded types or distinct value wrappers in the contract:
  ```ts
  export type UserId = string & { readonly __brand: "UserId" };
  ```

### 1.3 Explicit Error vs Result Contracts
- Decide and document in `contract.md` whether a failure mode is:
  - **Expected domain outcome:** Returned as a typed discriminated union (`Result<T, E>` or nullable/status union) so callers are forced by the type checker to handle the branch.
  - **Contract violation / unexpected fault:** Thrown as a typed `Error` subclass (with a stable `name`, `code`, or `message` pattern specified in `contract.md`) or rejected `Promise`.
- Never mix throwing and returning error objects for the same failure category within a single function.

### 1.4 Deterministic Boundaries & Injectable Dependencies
- **Injectable Reference Clock:** Any helper that computes expiration, TTLs, countdowns, rate-limit windows, or formatting relative to "now" **must** accept an optional injectable timestamp or clock parameter (e.g., `nowMs: number = Date.now()` or `deps?: { readonly now?: () => number }`). This guarantees 100% deterministic unit tests without flaky wall-clock sleeps.
- **Injectable Side-Effect Ports:** Functions that depend on randomness, environment variables, filesystem access, or network I/O should accept narrow structural interface dependencies (ports) with sensible production defaults so QA can inject pure in-memory fakes in unit tests.

---

## 2. Lintel Stub Files (`## Stub files` in `design.md`)

During the Design phase, the Architect creates or updates the source files listed under `## Stub files` in `design.md`. These stubs are verified immediately by QA and `lintel lock`.

### 2.1 Compile Cleanly (`tsc --noEmit` Passes, Never Exit 2)
- Every stub file must be valid TypeScript that compiles cleanly under `tsc --noEmit` with strict compiler flags (`strict: true`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes` if enabled).
- All exported types, interfaces, enums, constants, classes, and function signatures declared in `contract.md` must be present and exported from the exact module paths specified.
- Ensure all parameter names in stub functions are either prefixed with `_` (e.g., `_input: string, _nowMs?: number`) or referenced so `noUnusedParameters` never triggers a compiler error (`exit 2`).

### 2.2 Fail Assertions at `lintel lock` (`exit 1`, Never `exit 2`)
- `lintel lock` requires QA's newly authored tests to **execute and fail on assertions or runtime stub errors (`exit 1`)**, proving the tests actually exercise the new contract. If a test crashes during module import, syntax parsing, or TypeScript compilation (`exit 2`), `lintel lock` rejects it.
- **Functions & Methods:** Implement stub bodies by throwing `new Error("Not implemented")` (or returning a typed `Promise.reject(new Error("Not implemented"))` for `async` functions):
  ```ts
  export function formatCountdown(_expiresAtMs: number, _nowMs: number = Date.now()): string {
    throw new Error("Not implemented");
  }
  ```
- **React / UI Components (when applicable):** Render a minimal valid placeholder element (or throw inside event handlers) so component mounting and rendering harnesses can load the module without import/link errors while failing specific content/behavior assertions (`exit 1`).

### 2.3 Preserve Existing Exports & Baseline Green Tests
- When adding a new function or type to an existing module, **never delete, rename, or break existing exports** in that file unless the phase explicitly specifies a breaking migration.
- Existing baseline tests in `tests/` and `e2e/` must continue to pass when `lintel verify` runs against unaffected modules.

---

## 3. ESM vs CommonJS Module Boundaries

Module resolution mismatches are the #1 cause of `exit 2` runner crashes in Node.js and TypeScript repositories. The Architect must inspect `package.json` (`"type": "module"` vs CommonJS) and `tsconfig.json` (`"module"` and `"moduleResolution"`) before designing module paths.

- **`moduleResolution: "NodeNext"` / `"Node16"` (Native ESM):**
  - Relative runtime imports must include the compiled `.js` extension (`import { parse } from "./parser.js"`) even when authoring `.ts` files, unless the project uses a bundler resolution mode.
  - Use `import type { Foo } from "./types.js"` for type-only imports so runtime Node.js never attempts to load erased type declarations.
- **`moduleResolution: "Bundler"` (Next.js, Vite, modern tsx/tsup):**
  - Respect configured path aliases in `tsconfig.json` (`@/lib/...`) consistently across `src/` and `tests/`.
  - Avoid default exports (`export default`) for domain modules and utilities; **prefer named exports** (`export function`, `export type`) to prevent ESM/CJS `.default` interop ambiguity across test runners and bundlers. (Exception: framework-mandated default exports such as Next.js `page.tsx`, `layout.tsx`).
- **No Circular Value Dependencies:** Design a strict directed acyclic graph (DAG) across modules:
  `types / contracts` $\leftarrow$ `pure domain helpers (src/lib/)` $\leftarrow$ `application services` $\leftarrow$ `framework adapters / routes / UI`.

---

## 4. Pure Domain Modules (`src/lib/`) vs Framework Layers

Separate business rules and data transformations from framework lifecycle and transport concerns so core logic can be tested directly in fast, isolated unit tests.

### 4.1 Pure Domain Layer (`src/lib/` or `src/domain/`)
- Contains pure computations, state transitions, validation, parsers, formatters, cryptographic/token helpers, and domain models.
- **Zero framework imports:** Modules in `src/lib/` must never import `next/*`, `react`, `express`, `fastify`, `electron`, or browser DOM globals (`window`, `document`).
- Accepts plain TypeScript data structures and returns plain TypeScript data structures (or discriminated unions).

### 4.2 Framework & Transport Layer
- **Next.js App Router (`src/app/`):**
  - Keep Server Components, Route Handlers (`route.ts`), and Server Actions thin: parse/validate request inputs, invoke pure domain functions in `src/lib/`, and map domain results to `NextResponse` or JSX props.
  - Enforce Server vs Client boundaries: never import server-only modules (database clients, Node `fs`/`crypto`, secret env vars) into files marked `"use client"` or modules reachable from client components.
- **HTTP Servers (Express / Fastify / Hono):**
  - Separate app factory construction (`createApp(deps)`) from socket binding (`app.listen(port)`). Exporting `createApp` without side-effectful top-level `.listen()` allows integration tests to exercise routes in-process without port collisions.
  - Map domain discriminated union results to HTTP status codes and structured JSON error envelopes inside a thin controller or error-handling middleware.
- **Electron Desktop Apps:**
  - Enforce strict process isolation between Main, Preload, and Renderer processes. See [references/electron.md](references/electron.md) for detailed architecture and security invariants.

---

## 5. Test Outline Mapping (`## Test outline` in `design.md`)

Every `design.md` must include a `## Test outline` section with explicit bullets for `unit`, `integration`, and `e2e`. Never cite specific test filenames with extensions in `design.md` or skills; describe test scenarios and reference the `tests/` or `e2e/` directories generally.

- **`unit`:** Map all pure functions in `src/lib/`, discriminated union branches, boundary conditions, malformed inputs, and deterministic clock/timestamp edge cases to `unit`.
- **`integration`:** Map multi-module workflows, HTTP route handlers (`createApp`), database repositories, or IPC preload bridges to `integration`. If a phase only modifies pure domain logic or isolated UI state with no cross-module I/O boundary, write:
  `- integration: none: <clear architectural justification>`
- **`e2e`:** Map user-visible browser journeys, full HTTP server flows, or full application window lifecycles to `e2e`. If a phase only adds or updates a pure library helper in `src/lib/` with no UI or route surface changes, write:
  `- e2e: none: Pure library helper in src/lib/ with no user-facing UI or route changes; fully covered by unit tests`

---

## 6. Architect-Reviewer Checklist

Before approving `design.md`, `contract.md`, and the stub files:
1. **Contract Completeness:** Does `contract.md` specify exact module paths, named exports, readonly/discriminated types, error behavior, and optional injectable timestamps/dependencies for deterministic testing?
2. **Stub Verification (`tsc --noEmit` & `exit 1`):** Do all files listed under `## Stub files` exist, match `contract.md` signatures, pass `tsc --noEmit` with zero errors, and throw `new Error("Not implemented")` so `lintel lock` fails with `exit 1` and never `exit 2`?
3. **Baseline Integrity:** Are all pre-existing exports in modified files preserved intact?
4. **Layering & Purity:** Is core domain logic placed in `src/lib/` free of framework coupling? Are Electron main/preload/renderer boundaries isolated per [references/electron.md](references/electron.md)?
5. **Test Outline Coverage:** Does `## Test outline` explicitly cover `unit`, `integration`, and `e2e` (using `none: <reason>` only when architecturally justified)?
