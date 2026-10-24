---
name: java-design
description: Architectural and design patterns for modern Java (17/21+) and Lintel contract specification
---
# Java Design

Architectural and contract specification guidance for designing modern Java (17/21+) systems in Lintel. Use this skill as the **Architect** when authoring `contract.md` and creating compile-ready stub files, or as the **Architect-Reviewer** when auditing contracts and architectural boundaries before handoff to QA and Dev.

For Spring Boot-specific architecture (dependency injection, configuration properties, `ProblemDetail` error handling, transaction boundaries, and test slice design), consult [references/spring-boot.md](references/spring-boot.md).

---

## 1. Authoring `contract.md` for Java

In Lintel's TDD workflow, QA writes tests and Dev writes implementations concurrently against the contract without reading each other's code. Every ambiguity in `contract.md` causes compilation mismatches during `lintel lock`. Specify all public boundaries explicitly:

### Required Contract Elements
1. **Exact Package and Type Declarations:**
   - State the full package name (e.g., `package com.acme.billing.invoice;`) and exact source path under `src/main/java/...`.
   - Specify the exact kind of each type: `public final class`, `public interface`, `public sealed interface ... permits ...`, `public record`, or `public enum`.
2. **Complete Method and Constructor Signatures:**
   - Write exact Java signatures including parameter types, generic bounds, return types, and visibility modifiers (`public`, package-private).
   - For Java `record` types, list the canonical component names and types in exact order, along with any compact constructor validation rules or derived methods.
3. **Exception Hierarchy & Failure Semantics:**
   - Explicitly list every exception a method may throw and under what conditions (e.g., "Throws `IllegalArgumentException` if `amount` is negative or null; throws `InvoiceNotFoundException` if no record exists for `id`").
   - Distinguish **unchecked domain exceptions** (`extends RuntimeException`) from **checked exceptions** (`extends Exception`). Prefer unchecked exceptions for domain invariant violations and unrecoverable infrastructure errors; use checked exceptions only when callers are contractually required to recover immediately.
4. **Nullability & `Optional<T>` Semantics:**
   - Never leave nullability implicit. Document whether parameters reject `null` (via `Objects.requireNonNull` throwing `NullPointerException` or `IllegalArgumentException`).
   - Specify `Optional<T>` for return types that may legitimately be absent (e.g., repository lookups). Never use `Optional` for fields or method parameters.
   - Return empty collections (`List.of()`, `Set.of()`, `Map.of()`) rather than `null` for multi-valued results.
5. **Value Equality & Ordering:**
   - State how equality (`equals`/`hashCode`) and natural ordering (`Comparable<T>`) behave—especially when an entity is identified by an ID while a value object (`record`) uses structural equality.

---

## 2. Lintel Stub Rules (`## Stub files`)

When the Architect creates or modifies source files listed under `## Stub files` in `contract.md`, those stubs bridge the QA phase and `lintel lock`:

- **Must Compile Cleanly:** Every stub class, interface, record, and enum under `src/main/java` must compile without errors under Maven (`mvn test-compile`) or Gradle (`gradle testClasses`). Compilation errors cause `lintel lock` to fail with **exit code 2** (build/infrastructure error) instead of verifying that QA's tests fail on behavior (**exit code 1**).
- **Throw `UnsupportedOperationException` in Method Bodies:** Do not return dummy default values (`null`, `0`, `false`, `Optional.empty()`) that might accidentally pass negative or empty-case assertions in QA's suite. Every unimplemented method or constructor with behavior must fail deterministically at runtime:
  ```java
  package com.acme.billing.invoice;

  import java.time.Instant;
  import java.util.Optional;
  import java.util.UUID;

  public record Invoice(UUID id, Money total, Instant issuedAt) {
      public Invoice {
          throw new UnsupportedOperationException("Not implemented");
      }
  }

  public interface InvoiceRepository {
      Optional<Invoice> findById(UUID id);
      Invoice save(Invoice invoice);
  }

  public final class DefaultInvoiceService {
      private final InvoiceRepository repository;

      public DefaultInvoiceService(InvoiceRepository repository) {
          this.repository = repository;
      }

      public Invoice issueInvoice(UUID accountId, Money amount) {
          throw new UnsupportedOperationException("Not implemented");
      }
  }
  ```
- **Preserve Dependency Wiring:** Include required constructors and fields in stub classes so both QA unit harnesses and framework wiring compile without modification.

---

## 3. Modern Java (17/21+) Domain Modeling

### Value Objects with `record`
- Model immutable data carriers, commands, queries, events, and DTOs as `record` types.
- Enforce domain invariants inside the **compact constructor** (`public Money { ... }`) so invalid instances cannot be constructed.
- Defensively copy mutable components (such as `List`, `Set`, `Map`) in the compact constructor using `List.copyOf(...)`, `Set.copyOf(...)`, or `Map.copyOf(...)`.

### Algebraic Data Types with `sealed` Hierarchies
- Model closed sets of domain states, command outcomes, or failure modes using `sealed interface` and `record` implementations:
  ```java
  public sealed interface PaymentResult
          permits PaymentResult.Authorized, PaymentResult.Declined, PaymentResult.RetryableError {
      record Authorized(String transactionId, Instant processedAt) implements PaymentResult {}
      record Declined(String reasonCode) implements PaymentResult {}
      record RetryableError(String gatewayMessage, Duration retryAfter) implements PaymentResult {}
  }
  ```
- Combine sealed hierarchies with **exhaustive `switch` expressions and pattern matching** (Java 21) so adding a new subtype triggers a compile-time error until handled everywhere.

### Deterministic Time & Randomness
- **Never call `Instant.now()`, `LocalDate.now()`, or `UUID.randomUUID()` directly inside domain or service logic** when deterministic behavior is required by contract.
- Inject `java.time.Clock` (or an explicit ID generator interface / `Supplier<UUID>`) via constructor so QA tests can supply `Clock.fixed(instant, ZoneOffset.UTC)` without flaky timing windows.

---

## 4. Package-by-Feature & Clean Layering

### Package Organization
- Organize `src/main/java` **by business feature / bounded context**, not by technical layer:
  - **Preferred:** `com.acme.order`, `com.acme.payment`, `com.acme.shipping`
  - **Avoid:** top-level `com.acme.controllers`, `com.acme.services`, `com.acme.repositories`, `com.acme.models`
- Use **package-private** visibility (no modifier) for internal adapters, mappers, and helper classes inside a feature package. Expose only the feature's public facade, domain ports (interfaces), value records, and domain exceptions.

### Dependency Direction & Boundaries
- **Domain Core:** Pure Java types (`record`, entities, domain services, port interfaces, domain exceptions). Keep the domain core free of web, persistence, or messaging annotations where practical so business rules can be tested in fast, isolated unit suites.
- **Application / Use-Case Layer:** Orchestrates domain objects, ports (`Repository`, `Gateway`, `Clock`), and transaction boundaries.
- **Adapters (Inbound & Outbound):** REST controllers, CLI runners, message consumers, and JDBC/JPA/HTTP client adapters depend inward on application/domain interfaces. Never leak transport DTOs or persistence entities across the domain boundary.
- Mirror the feature package structure under the test source tree (`src/test/java`) so unit and slice tests can access package-private collaborators when appropriate, while separating fast `unit` suites from `integration` and `e2e` test groups.

---

## 5. Architect Review Checklist

Before approving a Java `contract.md` and its stubs:
- [ ] Are all package names, source paths under `src/main/java`, and type declarations (`class`, `interface`, `record`, `sealed`, `enum`) unambiguous?
- [ ] Do all method signatures specify exact parameter types, return types, `Optional` usage, and thrown exceptions?
- [ ] Do all listed `## Stub files` exist, compile cleanly with zero errors, and throw `new UnsupportedOperationException("Not implemented")` in unimplemented methods?
- [ ] Are time-sensitive operations designed around an injectable `java.time.Clock` rather than static system clock calls?
- [ ] Are mutable collections defensively copied or specified as unmodifiable in `record` types and return contracts?
