---
name: java-implementation
description: Modern Java (17/21+) implementation and code review standards for Lintel DEV and DEV-Reviewer agents
---
# Java Implementation Guide (Java 17 / 21+)

This skill governs production code implementation (`DEV`) and code review (`DEV-Reviewer`) for Java and Maven projects under Lintel.

---

## 1. Contract Adherence (`shared/phase-N/contract.md`)

`shared/phase-N/contract.md` is the authoritative specification binding `DEV` and `QA`. Because `QA` compiles and runs locked behavioral suites against your production types without viewing your implementation, any deviation breaks compilation or verification.

- **Exact Coordinates & Identifiers:** Match every package declaration, class/record/interface/enum name, generic type parameter, and visibility modifier (`public`, package-private, `protected`) verbatim.
- **Exact Method Signatures:** Match method names, parameter order, parameter types, return types, and declared checked exceptions (`throws` clauses) identically. Do not widen or narrow types or add undeclared checked exceptions.
- **Constructors & Factories:** Provide every constructor, static factory method, or builder required by the contract. When using a `record`, ensure canonical and compact constructors preserve the exact parameter types and validation semantics specified.
- **Behavioral Invariants & Edge Cases:** Implement all pre-conditions, post-conditions, null-handling contracts, exception types (e.g., `IllegalArgumentException`, `NullPointerException`, `IllegalStateException`, or custom domain exceptions), and boundary behaviors documented in the contract.
- **No Unilateral API Changes:** If you discover an ambiguity or defect in `shared/phase-N/contract.md`, do not silently alter public/package-private signatures. Escalate through the Lintel protocol so the contract can be revised cleanly.

---

## 2. Lintel Lane Discipline

Strict separation between production code and quality assurance is a core Lintel invariant:

- **Production Source Only:** Implement code strictly under `src/main/java` and `src/main/resources`.
- **Never Touch Test Paths:** Never create, edit, move, or delete files inside `src/test/` or integration test directories.
- **Never Read Locked QA Tests:** Do not read, inspect, or search QA's locked test suites, test reports, or compiled test outputs. Implement strictly from `shared/phase-N/contract.md` and production source context.
- **Dependency & Build Boundaries (`pom.xml`):**
  - Manage dependencies exclusively via `pom.xml` (never commit raw `.jar` files).
  - Do not modify `pom.xml` or `.mvn/` unless your phase plan and lane permissions explicitly authorize build configuration changes.
  - Never add, modify, or remove `<scope>test</scope>` dependencies or Surefire/Failsafe plugin configurations.

---

## 3. Modern Java Idioms (Java 17 / 21+)

Write clear, immutable-by-default, idiomatic modern Java:

### Immutability & Data Modeling
- **`record` for Value Types:** Use `record` for DTOs, value objects, events, commands, and immutable results. Use compact constructors for validation and normalization:
  ```java
  public record TransferRequest(String sourceAccountId, String targetAccountId, BigDecimal amount) {
      public TransferRequest {
          Objects.requireNonNull(sourceAccountId, "sourceAccountId must not be null");
          Objects.requireNonNull(targetAccountId, "targetAccountId must not be null");
          Objects.requireNonNull(amount, "amount must not be null");
          if (amount.compareTo(BigDecimal.ZERO) <= 0) {
              throw new IllegalArgumentException("amount must be positive");
          }
      }
  }
  ```
- **`final` Fields:** Mark class fields `private final` by default. Mutability should be an explicit, justified design choice.
- **Defensive Copies:** Never expose mutable internal collections or arrays directly, and never store caller-supplied mutable collections without copying. Use `List.copyOf(...)`, `Set.copyOf(...)`, and `Map.copyOf(...)` in constructors and accessors.

### Null Safety & Fail-Fast Validation
- **`Objects.requireNonNull`:** Validate non-null parameters at the entry point of public and package-private constructors and methods using `Objects.requireNonNull(param, "descriptive message")` unless the contract specifies another exception type.
- **`Optional<T>` Discipline:** Use `Optional<T>` exclusively as a return type to represent a potentially absent value. Never use `Optional` for fields or method parameters, and never call `.get()` without proving presence (prefer `.orElse(...)`, `.orElseGet(...)`, `.orElseThrow(...)`, or `.map(...)`).

### Sealed Types, Switch Expressions & Pattern Matching
- **Exhaustive `switch` Expressions:** Prefer `switch` expressions with arrow syntax (`->`) over fall-through `switch` statements.
- **Sealed Hierarchies & Pattern Matching:** Combine `sealed interface` / `permits` with pattern matching for `switch` (Java 21+) and record patterns so the compiler enforces exhaustiveness without a catch-all `default` branch:
  ```java
  public sealed interface PaymentOutcome permits PaymentOutcome.Approved, PaymentOutcome.Declined {
      record Approved(String transactionId, Instant processedAt) implements PaymentOutcome {}
      record Declined(String reasonCode) implements PaymentOutcome {}
  }

  public String formatReceipt(PaymentOutcome outcome) {
      Objects.requireNonNull(outcome, "outcome must not be null");
      return switch (outcome) {
          case PaymentOutcome.Approved(var txId, var at) -> "APPROVED:" + txId + "@" + at;
          case PaymentOutcome.Declined(var reason) -> "DECLINED:" + reason;
      };
  }
  ```
- **Local Variable Type Inference (`var`):** Use `var` for local variables when the concrete type is immediately obvious from the right-hand side. Avoid `var` when it obscures the type in chained pipelines or numeric literals.

### Time & Determinism (`java.time` + `Clock`)
- **Never Use Legacy Date/Time Types:** Never use `java.util.Date`, `java.util.Calendar`, `java.sql.Date`, or `SimpleDateFormat`.
- **Use `java.time` Everywhere:** Use `Instant`, `LocalDate`, `LocalDateTime`, `ZonedDateTime`, `Duration`, and `Period`, and format with thread-safe `DateTimeFormatter`.
- **Inject `java.time.Clock`:** Never hardcode `Instant.now()`, `LocalDate.now()`, or `System.currentTimeMillis()` inside domain or service logic without an injectable `Clock`. Provide a constructor accepting `Clock` (and, if required by the contract, a convenience constructor delegating to `Clock.systemUTC()`), enabling deterministic time control.

---

## 4. Resource Safety & I/O Cleanup

- **`try-with-resources` Everywhere:** Always manage `AutoCloseable` resources (`InputStream`, `OutputStream`, `Reader`, `Writer`, JDBC `Connection`/`PreparedStatement`/`ResultSet`, HTTP clients, channels) inside a `try-with-resources` block.
- **Filesystem Streams:** `Files.lines(...)`, `Files.walk(...)`, `Files.list(...)`, and `Files.find(...)` hold open OS file descriptors—always wrap those `Stream` instances in `try-with-resources`:
  ```java
  try (Stream<Path> paths = Files.list(directory)) {
      return paths.filter(Files::isRegularFile).sorted().toList();
  }
  ```
- **Explicit `Charset`:** Always specify `StandardCharsets.UTF_8` (or the contract-specified encoding) when converting between bytes and strings or opening readers/writers. Never rely on platform-default encodings.

---

## 5. Thread Safety & Concurrency

- **Stateless & Immutable Components:** Keep services, handlers, and domain calculators stateless with `final` dependencies so they are inherently thread-safe.
- **Concurrent Collections & Atomics:** When shared mutable state is required, use `ConcurrentHashMap`, `CopyOnWriteArrayList`, or `java.util.concurrent.atomic` (`AtomicLong`, `AtomicReference`, `LongAdder`) rather than unsynchronized `HashMap` or `ArrayList`. Ensure compound check-then-act operations use atomic methods (`computeIfAbsent`, `merge`, `compute`).
- **Preserve Interruption Status:** When catching `InterruptedException`, always restore the interrupt flag via `Thread.currentThread().interrupt()` before propagating or wrapping the exception.
- **Structured Concurrency & Virtual Threads (Java 21+):** Avoid unbounded thread creation. Use managed `ExecutorService` instances (closed cleanly via `try-with-resources` on Java 19+) and prefer virtual threads (`Executors.newVirtualThreadPerTaskExecutor()`) for blocking I/O workloads when appropriate.

---

## 6. Logging & Error Handling (SLF4J)

- **SLF4J Only:** Use `org.slf4j.Logger` and `org.slf4j.LoggerFactory`. Never use `System.out.println`, `System.err.println`, or `Throwable#printStackTrace()`.
- **Parameterized Logging:** Use SLF4J placeholders (`{}`) instead of string concatenation to avoid unnecessary allocations when a log level is disabled:
  ```java
  private static final Logger log = LoggerFactory.getLogger(OrderProcessor.class);

  log.info("Processed order id={} itemCount={} durationMs={}", orderId, items.size(), durationMs);
  log.error("Failed to persist order id={}", orderId, exception);
  ```
- **Never Leak Secrets or PII:** Never log passwords, API keys, bearer tokens, raw authorization headers, session secrets, cryptographic keys, or sensitive personal data in log messages or exception messages. Mask or omit sensitive fields in custom `toString()` implementations (including on `record` types that hold credentials or tokens).
- **Specific Exception Handling:** Catch the most specific exception type possible. Never swallow exceptions silently (`catch (Exception ignored) {}`), and always preserve the root cause (`new DomainException("message", cause)`) when translating exceptions across architectural boundaries.

---

## 7. Build, Verification & Completion Protocol

### For `DEV` (Before Signaling Completion)
1. **Compile & Build Production Code:**
   Run the Lintel build wrapper to compile via Maven (`./mvnw compile` under the hood):
   ```bash
   ./.lintel/scripts/build.sh
   ```
2. **Run Phase Verification:**
   Execute Lintel's phase check to compile and run the phase verification suite against your implementation:
   ```bash
   ./.lintel/bin/lintel check --phase <N>
   ```
3. **Complete Only When Green:**
   Never invoke `done --verdict complete` while `./.lintel/scripts/build.sh` or `./.lintel/bin/lintel check --phase <N>` reports compilation errors, test failures, or lane violations. Diagnose failures from the check output and `shared/phase-N/contract.md`—never by inspecting locked test files.

### For `DEV-Reviewer` (Review Checklist)
Before approving (`done --verdict approve`) or requesting changes (`done --verdict request-changes`), verify:
- [ ] **Contract Match:** Every package, type, record component, constructor, method signature, and exception in `shared/phase-N/contract.md` is implemented with exact fidelity.
- [ ] **Lane Integrity:** Zero modifications to `src/test/`, integration test directories, or unauthorized `pom.xml` scopes.
- [ ] **Immutability & Null Safety:** Fields are `final`, mutable collections are defensively copied with `List.copyOf`/`Map.copyOf`/`Set.copyOf`, and inputs are validated with `Objects.requireNonNull` or contract-defined checks.
- [ ] **Determinism & Time:** `java.time` types and `Clock` are used instead of legacy `Date`/`Calendar` or un-injected wall-clock calls.
- [ ] **Resource & Thread Safety:** All `AutoCloseable` and filesystem `Stream` resources use `try-with-resources`; shared state is thread-safe; `InterruptedException` restores the interrupt flag.
- [ ] **Safe Logging:** SLF4J parameterized logging (`{}`) is used with zero `System.out`/`System.err` calls and zero leaked secrets in logs or `record` `toString()` outputs.
- [ ] **Verification Passing:** Both `./.lintel/scripts/build.sh` and `./.lintel/bin/lintel check --phase <N>` pass cleanly.

