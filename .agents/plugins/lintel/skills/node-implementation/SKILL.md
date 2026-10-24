---
name: node-implementation
description: Coding standards and implementation practices in Node.js and TypeScript
---
# Node.js & TypeScript Implementation

Authoritative coding standards, architectural discipline, and verification practices for **DEV** and **DEV-Reviewer** in Node.js and TypeScript projects managed by Lintel.

---

## 1. Contract & Lane Discipline

- **Exact Contract Adherence:** Implement the exact module paths, exported symbol names (functions, classes, types, interfaces, constants, enums), parameter lists, default parameter values, property names, and return types specified in `shared/phase-N/contract.md`. Never rename exports, reorder parameters, or alter public signatures without an approved contract amendment.
- **Strict Lane Separation:** DEV owns production source code only.
  - **Never** create, modify, move, or delete files inside test directories (`tests/`, `e2e/`, `__tests__/`) or Lintel infrastructure (`.lintel/scripts/`, `.lintel/bin/`).
  - **Never** read QA's locked test suite during implementation to reverse-engineer assertions or hardcode test-specific return values. Implement general, sound logic derived strictly from `requirements.md`, `design.md`, and `contract.md`.
- **Module Resolution & Exports:** Respect the project's module system (`ESM` with explicit extensions vs `CommonJS` or bundler resolution) as defined in `package.json` and `tsconfig.json`. Prefer named exports unless `contract.md` explicitly specifies a default export. Avoid circular module dependencies across internal files.

---

## 2. Strict TypeScript Patterns

- **Strict Compiler Compliance:** All implementation code must compile with zero errors under `strict: true`, including `noImplicitAny`, `strictNullChecks`, `strictFunctionTypes`, `strictBindCallApply`, `noImplicitReturns`, and `exactOptionalPropertyTypes` (when enabled).
- **Zero Escape Hatches:**
  - Never use `any`, unsafe double assertions (`as unknown as T`), non-null assertions (`value!`) on unverified values, `@ts-ignore`, or `@ts-expect-error`.
  - Use `unknown` at untrusted boundaries (JSON parsing, external I/O, dynamic inputs, `catch (err: unknown)` blocks) and narrow via explicit runtime type guards (`typeof`, `instanceof`, `Array.isArray`, `Object.hasOwn`, custom `value is T` predicates, or schema validators).
- **Exhaustive Discriminated Unions:** Model domain states and outcomes with discriminated unions. Enforce compile-time exhaustiveness in `switch` statements using a `default` branch that assigns the discriminant to `never`:
  ```ts
  function assertNever(value: never): never {
    throw new Error(`Unhandled discriminated union member: ${String(value)}`);
  }
  ```
- **Immutability & Reference Safety:**
  - Declare variables with `const` by default; use `let` only when local loop or accumulator reassignment is required. Never use `var`.
  - Treat input objects, arrays, `Map`s, and `Set`s as `readonly` unless `contract.md` explicitly defines an in-place mutation contract. Return new references (`map`, `filter`, `slice`, spread, `structuredClone`) rather than mutating caller-owned structures or shared module-level state.
  - Under `exactOptionalPropertyTypes`, distinguish between omitting an optional key and setting a key to `undefined`. Construct objects conditionally or strip `undefined` properties when the target type forbids explicit `undefined`.

---

## 3. Defensive Numeric, Timestamp & String Handling

- **Numeric Validation & IEEE-754 Edge Cases:**
  - Remember that `typeof NaN === "number"` and `typeof Infinity === "number"`. Always validate numeric inputs with `typeof value === "number" && Number.isFinite(value)` before performing arithmetic.
  - Guard against division by zero, `NaN` propagation, `+Infinity` / `-Infinity`, and integer overflow beyond `Number.MAX_SAFE_INTEGER` (use `Number.isSafeInteger(n)` when discrete integers or counts are required, or `BigInt` when specified by the contract).
  - Normalize negative zero (`-0`) to positive zero (`0`) before returning or serializing numbers (e.g., `Object.is(result, -0) ? 0 : result` or `result === 0 ? 0 : result`), since `Object.is(-0, 0)` is `false` in strict equality assertions.
  - For monetary or fixed-precision decimal math, avoid raw floating-point accumulation errors (`0.1 + 0.2 !== 0.3`) by scaling to integer minor units (e.g., cents) and rounding deterministically.
- **Timestamp, Epoch & Timezone Precision:**
  - Verify the exact unit required by `contract.md`: Unix epoch **seconds** vs JavaScript `Date.now()` **milliseconds**. Convert explicitly (`Math.floor(ms / 1000)` or `seconds * 1000`) and apply the exact rounding mode (`Math.floor`, `Math.ceil`, `Math.round`, or `Math.trunc`) specified in the design.
  - Validate parsed dates with `Number.isFinite(date.getTime())` to catch `Invalid Date` instances before calling `.toISOString()` (which throws `RangeError` on invalid dates).
  - Perform calendar calculations in UTC (`getUTCFullYear`, `getUTCMonth`, `Date.UTC`) unless a specific IANA timezone or local time behavior is explicitly mandated by `contract.md`.
- **String, Unicode & Nullish Boundaries:**
  - Handle `null`, `undefined`, empty strings (`""`), and whitespace-only strings (`str.trim().length === 0`) gracefully according to `contract.md` without throwing unhandled `TypeError` exceptions.
  - Be mindful of UTF-16 code units (`str.length`) vs Unicode code points (`Array.from(str).length`) vs grapheme clusters (`Intl.Segmenter`) and UTF-8 byte length (`Buffer.byteLength(str, "utf8")`) when slicing, truncating, or enforcing size limits.
  - Use `Object.hasOwn(obj, key)` or `Object.create(null)` maps when indexing arbitrary string keys to prevent prototype pollution (`__proto__`, `constructor`, `toString`).

---

## 4. Asynchronous & Promise Safety

- **No Floating Promises:** Every `Promise` must be `await`ed, returned to the caller, or explicitly chained with `.catch(...)`. Never fire-and-forget asynchronous operations that can reject after a function returns.
- **Cancellation & Timeouts (`AbortSignal`):** Propagate `AbortSignal` to timers, `fetch`, streams, and long-running loops when supported by the contract. Check `signal.aborted` (or `signal.throwIfAborted()`) before starting expensive work and clean up abort listeners in `finally` blocks to avoid memory leaks.
- **Concurrency & Partial Failure (`Promise.allSettled`):**
  - Use `Promise.all` only when fail-fast cancellation is desired and sibling promises do not leak resources or leave unhandled rejections.
  - Use `Promise.allSettled` when running independent tasks where partial results or aggregated error reporting are required, or when cleanup must wait for all in-flight operations to settle.
- **Resource & Timer Cleanup:** Always clear `setTimeout` / `setInterval` handles, release locks, close file descriptors (`FileHandle.close()`), and destroy streams inside `try ... finally` blocks so resources are never leaked on error paths or keeps the Node.js event loop alive unexpectedly.

---

## 5. Error Handling, Security & Structured Logging

- **Contract-Compliant Errors:**
  - Throw the exact error class (`Error`, `TypeError`, `RangeError`, or custom domain error subclass) and preserve expected properties (`name`, `code`, `statusCode`, `cause`) specified in `contract.md`.
  - In `catch (err: unknown)` blocks, normalize unknown values safely (`err instanceof Error ? err : new Error(String(err))`) and attach `{ cause: err }` when wrapping lower-level failures.
- **Input Sanitization & Security:**
  - Never pass unsanitized user input to shell execution (`child_process.exec`), dynamic code evaluation (`eval`, `new Function`), or unparameterized queries.
  - Prevent path traversal by resolving and verifying file paths against an allowed base directory (`path.resolve` + prefix check).
  - Guard regular expressions against catastrophic backtracking (ReDoS) on untrusted strings.
- **Secrets & Clean Output:**
  - Never hardcode API keys, tokens, credentials, or private keys in source files.
  - Redact sensitive fields (`Authorization`, cookies, tokens, passwords, PII) from error messages and structured logs.
  - Remove all temporary `console.log` / `console.debug` statements before completing implementation so standard output and test runner streams remain clean.

---

## 6. Mandatory Gate Verification & Review Checklist

Before DEV transitions a phase or DEV-Reviewer approves an implementation:

1. **Contract & Diff Audit:**
   - Confirm `git status` and `git diff` show changes **only** in allowed production files—zero edits in `tests/`, `e2e/`, `__tests__/`, or `.lintel/`.
   - Verify every symbol, signature, and behavior in `shared/phase-N/contract.md` and `shared/phase-N/design.md` is implemented completely with no stubbed `TODO`s or hardcoded shortcuts.
2. **Build & Typecheck Gate:**
   - Run `./.lintel/scripts/build.sh` and confirm TypeScript compilation and build checks exit with code `0`.
3. **Isolated Phase Check Gate:**
   - Run `./.lintel/bin/lintel check --phase <N>` to execute the full automated test gate against your candidate tree in an isolated worktree.
   - If `lintel check --phase <N>` fails, diagnose the root cause from the reported failure output and contract specifications (without reading locked test source files), fix the production code, and re-run both gates.
4. **Completion Sign-Off:**
   - Only invoke `./.lintel/bin/lintel done --verdict complete` (or `--verdict approved` for DEV-Reviewer) after `./.lintel/bin/lintel check --phase <N>` succeeds for the exact current working tree state.
