# Spring Boot Architecture Reference

Architectural patterns and conventions for designing Spring Boot 3.x (Java 17/21+) applications in Lintel.

---

## 1. Dependency Injection & Bean Wiring

- **Constructor Injection Only:** Always design `@Service`, `@Component`, `@Repository`, and `@RestController` classes with a single constructor taking `final` dependencies. Omit `@Autowired` on single-constructor beans.
  - Constructor injection guarantees required dependencies are never `null`, keeps fields `final`, and allows QA unit tests to instantiate components directly with `new` without starting a Spring `ApplicationContext`.
- **Avoid Field and Setter Injection:** Never use `@Autowired` on fields in application or domain services; it hides dependencies and forces reflection or full container startup in unit tests.
- **Explicit `@Configuration` for Infrastructure & Core Beans:** When keeping core domain services free of Spring stereotype annotations, register them via `@Bean` factory methods in a feature `@Configuration` class, including shared infrastructure beans like `java.time.Clock`:
  ```java
  @Configuration
  public class TimeConfig {
      @Bean
      public Clock systemClock() {
          return Clock.systemUTC();
      }
  }
  ```

---

## 2. Type-Safe Configuration (`@ConfigurationProperties`)

- **Prefer `@ConfigurationProperties` over scattered `@Value` annotations:** Group related configuration settings into an immutable Java `record` bound via `@ConfigurationProperties` and validated with Jakarta Bean Validation (`@Validated`):
  ```java
  @Validated
  @ConfigurationProperties(prefix = "acme.payment.gateway")
  public record PaymentGatewayProperties(
      @NotBlank String baseUrl,
      @NotNull Duration connectTimeout,
      @Positive int maxRetries
  ) {}
  ```
- Enable binding via `@EnableConfigurationProperties(PaymentGatewayProperties.class)` on a `@Configuration` class (or `@ConfigurationPropertiesScan`).
- In `contract.md`, document both the property prefix/keys (`application.yml` / `application.properties`) and the exact `record` component signatures so QA and Dev agree on configuration binding.

---

## 3. Web Layer & RFC 9457 `ProblemDetail` Error Handling

- **Separate Transport DTOs from Domain Models:** Define request and response payloads as dedicated `record` types with Jakarta Validation annotations (`@Valid`, `@NotBlank`, `@Positive`, etc.) at the controller boundary. Never expose JPA `@Entity` classes directly in `@RestController` signatures.
- **Centralized Exception Translation with `@RestControllerAdvice` and `ProblemDetail`:**
  - Map domain exceptions to standard HTTP status codes and Spring 6 / Boot 3 `org.springframework.http.ProblemDetail` (RFC 9457) payloads in a `@RestControllerAdvice` class.
  - Specify the exact HTTP status code, problem `title`, `detail`, `type` URI, and any custom extension properties in `contract.md` so QA's MockMvc / WebTestClient assertions and Dev's exception handlers match:
  ```java
  @RestControllerAdvice
  public class BillingExceptionHandler extends ResponseEntityExceptionHandler {

      @ExceptionHandler(InvoiceNotFoundException.class)
      public ProblemDetail handleInvoiceNotFound(InvoiceNotFoundException ex) {
          ProblemDetail problem = ProblemDetail.forStatusAndDetail(
              HttpStatus.NOT_FOUND,
              ex.getMessage()
          );
          problem.setTitle("Invoice Not Found");
          problem.setProperty("invoiceId", ex.getInvoiceId());
          return problem;
      }
  }
  ```

---

## 4. Transaction Boundaries & Persistence Architecture

- **Place `@Transactional` at the Application Service Layer:**
  - Annotate use-case / service methods with `@Transactional` (and read-only query services with `@Transactional(readOnly = true)`).
  - Do not place `@Transactional` on `@RestController` classes (keeps database connections open during view rendering/serialization) or rely solely on repository-level transactions when a use case spans multiple writes.
- **Keep External I/O Outside Database Transactions:**
  - Never perform slow synchronous HTTP calls, RPCs, or message broker publishes inside an active `@Transactional` database boundary, as this exhausts connection pools and risks inconsistent state on rollback. Use transactional outbox patterns, `@TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)`, or two-step orchestration instead.
- **Persistence Boundary Separation:**
  - Keep JPA `@Entity` lifecycle concerns (lazy loading, dirty checking, `@GeneratedValue`) inside the persistence adapter or service boundary. Convert results to immutable domain `record`s or projections before returning to callers above the service layer.

---

## 5. Designing for Testability: Unit vs Slice vs Integration Suites

Structure components so each layer can be verified at the fastest appropriate test tier under `src/test/java` across Lintel's configured test groups (`unit`, `integration`, `e2e`):

1. **Pure Domain & Service Unit Suites (`unit` group):**
   - Domain records, sealed hierarchies, and constructor-injected application services should be testable with plain JUnit 5, AssertJ, and Mockito—zero Spring context startup required.
2. **Spring Boot Test Slices (`unit` or fast slice tier):**
   - **Web Slice (`@WebMvcTest` / `@WebFluxTest`):** Tests a single `@RestController`, `@RestControllerAdvice`, validation rules, and JSON serialization in isolation by mocking the application service `@MockitoBean` / `@MockBean`.
   - **Persistence Slice (`@DataJpaTest` / `@JdbcTest`):** Tests repository queries, schema mappings, and constraints against an embedded or containerized database without loading web or service beans.
   - **JSON Slice (`@JsonTest`):** Verifies Jackson serialization/deserialization formats for complex `record` DTOs.
   - **Tip:** Keep `@EnableJpaAuditing` or global security filters in dedicated `@Configuration` classes rather than on the main `@SpringBootApplication` class so `@WebMvcTest` slices can load cleanly without requiring JPA beans.
3. **Full Integration Suites (`integration` group):**
   - Use `@SpringBootTest` (often paired with `@Testcontainers` / `@ServiceConnection`) sparingly to verify end-to-end wiring, transaction rollback behavior, and real infrastructure adapters.
