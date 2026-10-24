---
name: java-testing
description: Comprehensive Java 21+ testing guide (JUnit 5, AssertJ, Mockito, Spring Boot Test, Testcontainers, Maven Surefire/Failsafe) for Lintel QA and QA-Reviewer
---
# Java Testing

Comprehensive conventions and patterns for writing and reviewing Java test suites in a Lintel TDD workflow.

## Core Testing Stack

- **Test Runner:** JUnit 5 (Jupiter) — `@Test`, `@ParameterizedTest`, `@Nested`, `@DisplayName`.
- **Assertions:** AssertJ (`org.assertj.core.api.Assertions.*`) for fluent, type-safe assertions. Prefer AssertJ over `org.junit.jupiter.api.Assertions`.
- **Mocking:** Mockito (`@ExtendWith(MockitoExtension.class)`) strictly for external boundaries and ports.
- **Integration & Containers:** Spring Boot Test slice annotations (`@WebMvcTest`, `@DataJpaTest`) and Testcontainers (`@Testcontainers`, `@Container`, `@ServiceConnection`).
- **Build Plugins:** Maven Surefire for unit tests (`*Test`, `*Tests` in `src/test/java`) and Maven Failsafe for integration tests (`*IT` in `src/test/java` or `src/it/java`).

---

## Writing Tests for `lintel lock` Against Architect Stubs

During the QA phase (before `lintel lock`), the Architect has established production type signatures, records, interfaces, and method stubs that throw `UnsupportedOperationException("Not implemented")` or return placeholder values.

### Mandatory Rules for Pre-Lock Tests

1. **Must Compile Cleanly (No Exit Code `2`):**
   - Import and invoke the exact package names, class/record names, method signatures, and checked exceptions defined by the Architect.
   - Never invent non-existent methods, types, or constructors in tests.
2. **Must Fail with Exit Code `1` Against Stubs:**
   - Every test targeting unimplemented production behavior must execute and fail (either via an uncaught `UnsupportedOperationException` bubbling out of the stub or an AssertJ assertion failure on a placeholder return value).
   - Surefire treats an uncaught `UnsupportedOperationException` during test execution as a test error/failure, exiting with code `1`.
3. **NEVER Assert on the Stub's `UnsupportedOperationException`:**
   - Do **not** write `assertThatThrownBy(() -> service.process(cmd)).isInstanceOf(UnsupportedOperationException.class)`. A test that passes against the stub violates TDD and will fail once Dev implements the real logic!
   - Always assert on the **real expected domain return value, state transition, or domain exception** specified by the contract.

```java
// WRONG — passes against Architect stub and breaks once Dev implements the method
@Test
void shouldProcessOrder() {
    assertThatThrownBy(() -> orderService.placeOrder(validCommand))
        .isInstanceOf(UnsupportedOperationException.class);
}

// RIGHT — compiles cleanly, fails with exit 1 against Architect stub, passes when Dev implements
@Test
void shouldReturnConfirmedReceiptWhenPlacingValidOrder() {
    OrderReceipt receipt = orderService.placeOrder(validCommand);

    assertThat(receipt.status()).isEqualTo(OrderStatus.CONFIRMED);
    assertThat(receipt.totalAmount()).isEqualByComparingTo(new BigDecimal("149.99"));
}
```

---

## Lintel Exit Code Contract (`0` / `1` / `2` / `3`)

Every Lintel test group script (`unit`, `integration`, `e2e`) must adhere to the strict 4-code contract:

| Exit Code | Meaning | Typical Cause in Maven / Java |
| :--- | :--- | :--- |
| `0` | **Pass** | All executed tests in the group passed (`BUILD SUCCESS` with `Tests run: N` where `N > 0` and `Failures: 0, Errors: 0`). |
| `1` | **Test Failure** | Tests compiled and ran, and at least one assertion failed or threw an unexpected runtime exception (including `UnsupportedOperationException` from Architect stubs). |
| `2` | **Compilation / Setup Error** | `maven-compiler-plugin:testCompile` failed, application context failed to boot in E2E, or Docker/Testcontainers daemon is unreachable. |
| `3` | **No Tests Found** | No test classes matched the group filter (`Tests run: 0`). |

### Group Separation & Maven Execution Recipes

#### 1. Unit Tests
- **Location & Naming:** Place unit test classes in `src/test/java` matching `*Test` or `*Tests` (excluding `*IT`).
- **Reports:** `target/surefire-reports`
- **Maven Execution Recipe:**
  1. Compile sources and tests first (`./mvnw test-compile -q`) — map failure to exit code `2`.
  2. Run Surefire with fail-if-no-tests enabled (`./mvnw surefire:test -DfailIfNoTests=true`).
  3. Inspect `target/surefire-reports` XML/txt summaries: if zero test cases were discovered or executed, return exit code `3`; if failures or errors > 0, return `1`; otherwise return `0`.

#### 2. Integration Tests
- **Location & Naming:** Place integration test classes matching `*IT` in `src/test/java` or under `src/it/java`.
- **Reports:** `target/failsafe-reports`
- **Maven Execution Recipe:**
  1. Compile first (`./mvnw test-compile -q`) — map failure to exit code `2`.
  2. Execute Failsafe (`./mvnw failsafe:integration-test failsafe:verify -DfailIfNoTests=true`).
  3. Map zero discovered `*IT` classes to exit code `3`, test failures/errors in `target/failsafe-reports` to `1`, and clean pass to `0`.

#### 3. E2E Tests
- **Focus:** Full application lifecycle (dynamic port binding via `RANDOM_PORT` or packaged artifact startup, readiness probe check, and HTTP/API assertions).
- **Exit Code Mapping:**
  - `0`: All E2E scenarios passed.
  - `1`: One or more E2E assertions failed.
  - `2`: Application failed to start, compilation failed, or health check timed out.
  - `3`: No E2E test classes or scenarios exist.

---

## JUnit 5 & Parameterized Boundary Matrices

Use `@Nested` classes to group behavior by method or scenario, and `@ParameterizedTest` with `@CsvSource` or `@MethodSource` to exhaustively cover boundary matrices without duplicating test methods.

### Standard Boundary Checklist
Always test applicable domain boundaries:
- **Strings:** `null`, `""` (empty), `"   "` (blank/whitespace-only), max-length boundary, `max + 1`, Unicode/special characters.
- **Numerics (`int`, `long`, `BigDecimal`):** `0`, `-1`, `1`, `Integer.MIN_VALUE`, `Integer.MAX_VALUE`, `new BigDecimal("0.00")` vs `BigDecimal.ZERO` (scale differences!), negative amounts, >2 decimal places.
- **Collections:** `null`, `List.of()` (empty), single-element, duplicate elements, unmodifiable collections.
- **Time:** exact instant boundaries (inclusive start vs exclusive end), leap days, timezone offsets.

### `@ParameterizedTest` with `@CsvSource` and `@MethodSource`

```java
import java.math.BigDecimal;
import java.util.stream.Stream;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

// Standard JUnit Jupiter annotations: @DisplayName, @Nested, @ParameterizedTest,
// @CsvSource, @MethodSource, @NullAndEmptySource, @ValueSource
class PricingCalculatorUnitSpecs {

    private final PricingCalculator calculator = new PricingCalculator();

    @Nested
    @DisplayName("calculateDiscountedTotal boundaries")
    class CalculateDiscountedTotal {

        @ParameterizedTest(name = "subtotal={0}, discountPct={1} -> expected={2}")
        @CsvSource({
            "100.00,  0, 100.00",
            "100.00, 15,  85.00",
            "100.00, 100,  0.00",
            "  0.01, 50,   0.01",
            " 33.33, 10,  30.00"
        })
        void shouldComputeRoundedTotalAcrossBoundaryMatrix(
                BigDecimal subtotal, int discountPercent, BigDecimal expectedTotal) {
            BigDecimal actual = calculator.calculateDiscountedTotal(subtotal, discountPercent);

            // Use isEqualByComparingTo for BigDecimal value equality, or isEqualTo when exact scale is required
            assertThat(actual)
                .isEqualByComparingTo(expectedTotal)
                .hasScaleOf(2);
        }

        @ParameterizedTest(name = "invalid input: subtotal={0}, discountPct={1}")
        @MethodSource("invalidPricingInputs")
        void shouldRejectOutOfRangeInputs(BigDecimal subtotal, int discountPercent, String expectedMessagePart) {
            assertThatThrownBy(() -> calculator.calculateDiscountedTotal(subtotal, discountPercent))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining(expectedMessagePart);
        }

        static Stream<Arguments> invalidPricingInputs() {
            return Stream.of(
                Arguments.of(null, 10, "subtotal"),
                Arguments.of(new BigDecimal("-0.01"), 10, "subtotal"),
                Arguments.of(new BigDecimal("50.00"), -1, "discountPercent"),
                Arguments.of(new BigDecimal("50.00"), 101, "discountPercent")
            );
        }
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {" ", "\t", "\n"})
    void shouldRejectNullOrBlankCouponCodes(String invalidCode) {
        assertThatThrownBy(() -> calculator.applyCoupon(invalidCode))
            .isInstanceOf(IllegalArgumentException.class);
    }
}
```

---

## AssertJ Fluent Assertions

Always use AssertJ (`org.assertj.core.api.Assertions.assertThat` and `assertThatThrownBy`) for expressive failure messages and rich collection/exception inspection:

```java
// Record & object assertions
assertThat(result)
    .isNotNull()
    .extracting(OrderSummary::id, OrderSummary::status)
    .containsExactly(expectedId, OrderStatus.PAID);

// Collection assertions with tuple extraction
assertThat(invoice.lineItems())
    .hasSize(2)
    .extracting(LineItem::sku, LineItem::quantity)
    .containsExactly(
        tuple("SKU-100", 2),
        tuple("SKU-200", 1)
    );

// Exception assertions — always verify the specific exception type (and message/fields when part of the contract)
assertThatThrownBy(() -> inventoryService.reserve("SKU-999", 5))
    .isInstanceOf(InsufficientStockException.class)
    .hasMessageContaining("SKU-999")
    .extracting("availableQuantity")
    .isEqualTo(0);

// BigDecimal assertions — never use .isEqualTo(new BigDecimal("10")) if scale can vary unless testing scale
assertThat(balance).isEqualByComparingTo("10.00");
```

---

## Deterministic Time Testing with `Clock.fixed(...)`

Never call `Instant.now()`, `LocalDate.now()`, or `Thread.sleep(...)` in unit tests to verify time-dependent behavior. Inject a `java.time.Clock` into production components and pass `Clock.fixed(...)` (or a mutable test clock) in tests:

```java
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class TokenValidatorUnitSpecs {

    private static final Instant FIXED_NOW = Instant.parse("2026-03-15T12:00:00Z");
    private final Clock fixedClock = Clock.fixed(FIXED_NOW, ZoneOffset.UTC);
    private final TokenValidator validator = new TokenValidator(fixedClock);

    @Test
    void shouldTreatTokenExpiringAtExactCurrentInstantAsExpired() {
        Token tokenAtBoundary = new Token("abc", FIXED_NOW);
        Token tokenOneMillisecondLater = new Token("def", FIXED_NOW.plusMillis(1));

        assertThat(validator.isValid(tokenAtBoundary)).isFalse();
        assertThat(validator.isValid(tokenOneMillisecondLater)).isTrue();
    }
}
```

---

## Mocking Guidelines (Mockito)

- **Mock Only External Ports:** Use `@ExtendWith(MockitoExtension.class)` and `@Mock` only for outbound ports (repositories, HTTP clients, message publishers).
- **Never Mock Value Objects or Records:** Instantiate real domain records, commands, events, and pure domain policies.
- **Avoid Over-Verification:** Prefer asserting on returned values or state changes. Use `verify(mock).method(...)` or `@Captor` only when the outbound side effect *is* the contract being tested.

```java
@ExtendWith(MockitoExtension.class)
class NotificationDispatcherUnitSpecs {

    @Mock
    private EmailGateway emailGateway;

    @Captor
    private ArgumentCaptor<EmailMessage> messageCaptor;

    @Test
    void shouldSendFormattedReceiptEmailWhenOrderConfirmed() {
        var dispatcher = new NotificationDispatcher(emailGateway);
        var event = new OrderConfirmedEvent("ord-42", "user@example.com", new BigDecimal("25.00"));

        dispatcher.onOrderConfirmed(event);

        verify(emailGateway).send(messageCaptor.capture());
        EmailMessage sent = messageCaptor.getValue();
        assertThat(sent.recipient()).isEqualTo("user@example.com");
        assertThat(sent.subject()).contains("ord-42");
    }
}
```

---

## Spring Boot Test & Testcontainers (Integration Tests)

- **Prefer Narrow Test Slices:**
  - `@WebMvcTest(OrderController.class)` with `MockMvc` for HTTP routing, serialization, validation (`@Valid`), and status code mapping.
  - `@DataJpaTest` or `@JdbcTest` with Testcontainers for repository queries, constraints, and transactions.
  - `@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)` only for full-stack integration or E2E verification.
- **Use `@ServiceConnection` with Testcontainers (Spring Boot 3.1+):** Automatically wires container JDBC/Redis/Kafka properties without manual `@DynamicPropertySource` boilerplate.

```java
@DataJpaTest
@Testcontainers
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
class OrderRepositoryIntegrationSpecs {

    @Container
    @ServiceConnection
    static final PostgreSQLContainer<?> POSTGRES =
        new PostgreSQLContainer<>("postgres:16-alpine");

    @Autowired
    private OrderRepository orderRepository;

    @Test
    void shouldPersistAndLoadOrderByExternalReference() {
        OrderEntity saved = orderRepository.save(new OrderEntity("REF-100", OrderStatus.PENDING));

        assertThat(orderRepository.findByExternalReference("REF-100"))
            .isPresent()
            .get()
            .extracting(OrderEntity::getId, OrderEntity::getStatus)
            .containsExactly(saved.getId(), OrderStatus.PENDING);
    }
}
```

---

## QA & QA-Reviewer Checklist

- [ ] **No Stub-Exception Assertions:** No test asserts `isInstanceOf(UnsupportedOperationException.class)` to pass against an Architect stub.
- [ ] **Compiles Cleanly Against Stubs:** `./mvnw test-compile` exits `0` before `lintel lock`.
- [ ] **Fails Against Unimplemented Stubs:** Running the unit test group against Architect stubs exits `1` (never `0`, `2`, or `3`).
- [ ] **Boundary Coverage:** `@ParameterizedTest` covers null, empty, blank, zero, negative, and exact boundary values specified by the design contract.
- [ ] **Deterministic Execution:** Time uses `Clock.fixed(...)`, randomness is seeded or injected, and test order is independent.

