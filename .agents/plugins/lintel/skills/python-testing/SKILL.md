---
name: python-testing
description: Comprehensive Python testing guide (pytest, unittest, fixtures, parametrize, isolation, and Lintel gate contracts) for QA and QA-Reviewer
---
# Python Testing

Comprehensive testing standards, patterns, and verification rules for **QA** and **QA-Reviewer** in Python projects using `pytest` and `unittest`.

---

## 1. Lintel Exit Code Contract (`0` / `1` / `2` / `3`)

Every Lintel test group runner script (`unit`, `integration`, `e2e`) must enforce the 4-code exit contract:

| Lintel Exit Code | Meaning | `pytest` Native Exit Code | Typical Cause in Python |
| :--- | :--- | :--- | :--- |
| `0` | **Pass** | `0` | All collected tests executed and passed. |
| `1` | **Test / Assertion Failure** | `1` | Tests collected cleanly and ran, and at least one `assert` failed or a test raised an unhandled runtime exception (including `NotImplementedError` from Architect stubs). |
| `2` | **Collection / Import / Syntax Error** | `2`, `3`, `4` | `SyntaxError`, `ImportError`, `ModuleNotFoundError`, broken fixture setup during collection, internal runner error (`3`), or invalid CLI invocation (`4`). |
| `3` | **No Tests Collected** | `5` | Zero test items matched the group path or filter. **Must be mapped from `pytest` exit code `5` to `3` in the runner script.** |

### Runner Translation Pattern

Because `pytest` returns exit code `5` when no tests are collected and `2`/`3`/`4` on interruption, internal errors, or usage errors, Lintel test group scripts translate `pytest` exit codes as follows:

```bash
set +e
python3 -m pytest tests/unit
rc=$?
set -e

case "$rc" in
  0) exit 0 ;;
  1) exit 1 ;;
  5) exit 3 ;;
  *) exit 2 ;;
esac
```

- Always run suites through Lintel's configured test group scripts so exit code translation and environment isolation are preserved.
- Treat any exit code `2` as an immediate blocker: resolve broken imports, missing symbols, or syntax errors before attempting `lintel lock`.

---

## 2. Writing Tests for `lintel lock` (Red-on-Stub, Green-on-Spec)

During the QA phase (prior to `lintel lock`), the Architect defines the package hierarchy, classes, dataclasses, custom exceptions, type annotations, and callable stubs in `src/` that raise `NotImplementedError` or return placeholder values.

### Mandatory Invariants for Pre-Lock Tests

1. **Collect Cleanly Without `ImportError` (Never Exit `2`):**
   - Import the exact module paths, class names, function names, and exception types specified in `shared/phase-N/contract.md`.
   - Never invent uncontracted helper functions, extra keyword arguments, or private module paths that fail at import or collection time.
2. **Fail with Exit Code `1` Against Stubs (Red-on-Stub):**
   - When executed against the Architect's stubs, tests targeting unimplemented behavior must execute and fail with exit code `1` — either because calling the stub raises an unhandled `NotImplementedError` or because the returned placeholder fails a value assertion.
   - `pytest` records an unhandled `NotImplementedError` raised inside a test body as a test failure (`FAILED`) and exits with code `1`.
3. **NEVER Assert `NotImplementedError` on Stubs:**
   - Never write `with raises(NotImplementedError):` (or `self.assertRaises(NotImplementedError)`) to test an Architect stub.
   - A test that expects `NotImplementedError` will **pass (`exit 0`) against the unimplemented stub**, causing `lintel lock` to reject the suite (or causing the test to fail once Dev implements the specification).
4. **Pass with Exit Code `0` on Spec Implementation (Green-on-Spec):**
   - Assert the real domain return values, state mutations, side effects, and domain-specific exceptions required by `shared/requirements.md` and `shared/phase-N/contract.md`.

```python
from decimal import Decimal
from pytest import raises
from billing.models import InvoiceRequest, InvoiceStatus
from billing.service import issue_invoice
from billing.errors import InvalidInvoiceError


# WRONG — passes against the Architect stub and breaks once Dev implements the spec!
def should_issue_invoice_wrong():
    request = InvoiceRequest(account_id="acc-100", amount=Decimal("49.95"))
    with raises(NotImplementedError):
        issue_invoice(request)


# RIGHT — imports cleanly, fails with exit 1 on NotImplementedError stub, passes on spec
def should_issue_confirmed_invoice_for_valid_request():
    request = InvoiceRequest(account_id="acc-100", amount=Decimal("49.95"))

    receipt = issue_invoice(request)

    assert receipt.account_id == "acc-100"
    assert receipt.status == InvoiceStatus.ISSUED
    assert receipt.total == Decimal("49.95")


# RIGHT — asserts the contracted domain exception, NOT NotImplementedError
def should_reject_non_positive_invoice_amount():
    request = InvoiceRequest(account_id="acc-100", amount=Decimal("0.00"))

    with raises(InvalidInvoiceError, match="amount must be positive"):
        issue_invoice(request)
```

---

## 3. Boundary & Equivalence Matrices with `@mark.parametrize`

Use `@mark.parametrize` and `param(..., id="...")` (imported via `from pytest import approx, mark, param, raises`) to cover equivalence classes and exact boundaries (`t - 1`, `t`, `t + 1`) without duplicating test functions.

### Standard Python Boundary Checklist

- **Strings:** `""` (empty), `"   "` / `"\t\n"` (whitespace-only), single character, exact maximum length `N`, `N + 1`, non-ASCII / Unicode characters.
- **Integers & Floats:** `0`, `-1`, `1`, exact threshold `t`, `t - 1`, `t + 1`, `float("inf")`, `float("-inf")`, `float("nan")`; use `approx` for floating-point assertions.
- **`Decimal`:** `Decimal("0.00")`, negative `Decimal("-0.01")`, high-precision inputs requiring rounding (`Decimal("33.335")`), exact scale preservation.
- **Collections & Optional Values:** `None`, empty (`[]`, `{}`, `set()`, `()`), single element, duplicate elements, large inputs.

```python
from decimal import Decimal
from pytest import approx, mark, param, raises
from pricing.discounts import compute_net_price, DiscountPolicyError


@mark.parametrize(
    ("subtotal", "rate_pct", "expected_net"),
    [
        param(Decimal("100.00"), 0, Decimal("100.00"), id="zero-discount-boundary"),
        param(Decimal("100.00"), 15, Decimal("85.00"), id="standard-discount"),
        param(Decimal("100.00"), 100, Decimal("0.00"), id="full-discount-upper-boundary"),
        param(Decimal("0.01"), 50, Decimal("0.01"), id="sub-cent-half-up-rounding"),
        param(Decimal("33.33"), 10, Decimal("30.00"), id="repeating-decimal-rounding"),
    ],
)
def should_compute_net_price_across_boundary_matrix(
    subtotal: Decimal, rate_pct: int, expected_net: Decimal
) -> None:
    actual = compute_net_price(subtotal=subtotal, rate_pct=rate_pct)
    assert actual == expected_net


@mark.parametrize(
    ("subtotal", "rate_pct", "error_fragment"),
    [
        param(Decimal("-0.01"), 10, "subtotal", id="negative-subtotal-below-zero"),
        param(Decimal("50.00"), -1, "rate_pct", id="rate-below-lower-bound"),
        param(Decimal("50.00"), 101, "rate_pct", id="rate-above-upper-bound"),
    ],
)
def should_reject_out_of_range_discount_inputs(
    subtotal: Decimal, rate_pct: int, error_fragment: str
) -> None:
    with raises(DiscountPolicyError, match=error_fragment):
        compute_net_price(subtotal=subtotal, rate_pct=rate_pct)


def should_calculate_utilization_ratio_within_tolerance() -> None:
    ratio = 1 / 3
    assert ratio == approx(0.3333333, rel=1e-6)
```

---

## 4. Isolated Filesystem & Environment Testing (`tmp_path` & `monkeypatch`)

Lintel gate verification checks that running tests leaves the git worktree completely clean. Tests must never create, modify, or leave behind untracked files in the repository root, `src/`, or `tests/`.

### Filesystem Isolation with `tmp_path`

- Always pass `pytest`'s built-in `tmp_path` (or `tmp_path_factory` for session/module scope) to any component that reads or writes files, SQLite databases, lockfiles, or local caches.
- Never hardcode relative paths like `"./output.json"` or `"./cache.db"` without anchoring them to `tmp_path` or switching the working directory via `monkeypatch.chdir(tmp_path)`.

### Environment & Global State Isolation with `monkeypatch`

- Never mutate `os.environ`, `sys.path`, or module globals directly in a test body without automatic teardown.
- Use `monkeypatch.setenv("VAR", "value")` and `monkeypatch.delenv("VAR", raising=False)` to test configuration loading across present, missing, and malformed environment variables.
- Use `monkeypatch.chdir(tmp_path)` when testing CLI commands or relative-path resolution.

```python
from pathlib import Path
from pytest import MonkeyPatch, raises
from config_loader.settings import load_app_settings, SettingsError


def should_load_settings_from_isolated_directory_and_env(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_TIMEOUT_SECONDS", "30")
    monkeypatch.delenv("APP_DEBUG_OVERRIDE", raising=False)

    cfg_file = tmp_path / "settings.json"
    cfg_file.write_text('{"service_name": "billing-worker"}', encoding="utf-8")

    settings = load_app_settings(cfg_file)

    assert settings.service_name == "billing-worker"
    assert settings.environment == "production"
    assert settings.timeout_seconds == 30
    assert settings.debug is False


def should_fail_when_required_env_var_is_missing(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    cfg_file = tmp_path / "settings.json"
    cfg_file.write_text('{"service_name": "billing-worker"}', encoding="utf-8")

    with raises(SettingsError, match="APP_ENV"):
        load_app_settings(cfg_file)
```

---

## 5. Deterministic Clock, Randomness, and Network Mocking

Tests must be 100% deterministic, offline, and order-independent.

### Deterministic Time & Clocks

- Never call `time.sleep(...)` or depend on live wall-clock `datetime.now()` / `time.monotonic()` to test expiration, TTLs, rate limits, or retry backoff.
- **Preferred:** Pass a fixed `datetime` (`datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)`) or an injectable `clock` callable when part of the Architect's contract.
- **When patching module time:** Patch the `datetime` or `time` reference in the module under test via `monkeypatch.setattr` so exact boundary instants (`expires_at - 1s`, `expires_at`, `expires_at + 1s`) are tested deterministically.

### Network & External Boundary Mocking

- Never make live HTTP requests, open external sockets, or spawn external network services in unit tests.
- Mock only external boundaries (HTTP clients, database drivers, SMTP gateways, subprocesses). Never mock pure domain dataclasses, value objects, or the function under test.
- Always use `autospec=True` (with `mock.patch` or `mock.create_autospec`) so tests fail immediately if a mock is called with arguments that do not match the Architect's signature.

```python
from datetime import datetime, timedelta, timezone
from unittest import mock
from pytest import MonkeyPatch
from auth.tokens import TokenVerifier, TokenRecord
from notifications.client import WebhookClient, WebhookDispatcher


FIXED_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def should_treat_token_at_exact_expiry_instant_as_expired() -> None:
    verifier = TokenVerifier(now_fn=lambda: FIXED_NOW)

    expired_token = TokenRecord(token_id="tok-1", expires_at=FIXED_NOW)
    valid_token = TokenRecord(
        token_id="tok-2", expires_at=FIXED_NOW + timedelta(milliseconds=1)
    )

    assert verifier.is_active(expired_token) is False
    assert verifier.is_active(valid_token) is True


def should_dispatch_signed_payload_to_webhook_client() -> None:
    client_mock = mock.create_autospec(WebhookClient, instance=True)
    client_mock.post_json.return_value = 202
    dispatcher = WebhookDispatcher(client=client_mock)

    delivered = dispatcher.notify_order_paid(order_id="ord-77", total_cents=1500)

    assert delivered is True
    client_mock.post_json.assert_called_once_with(
        endpoint="/events/order-paid",
        payload={"order_id": "ord-77", "total_cents": 1500},
    )
```

### Standard Library `unittest` Suites

When a repository or test group uses `unittest` (`from unittest import TestCase, mock`), apply the same Lintel invariants:
- Use `self.assertEqual`, `self.assertRaises`, and `self.subTest(...)` (for boundary matrices).
- Use `tempfile.TemporaryDirectory()` and `self.addCleanup(...)` for filesystem and mock cleanup so worktree cleanliness is preserved.

---

## 6. Test Organization & Fixture Hygiene

- **Directory Structure:** Place test modules under the configured `tests/` directory hierarchy (such as `tests/unit/`, `tests/integration/`, `tests/e2e/`) matching the project's `.lintel/config.jsonc` test group globs.
- **Fixture Scope & Cleanup:** Keep fixtures in shared fixture modules narrow (`scope="function"` by default) so mutable state never leaks between tests. Use `yield` fixtures to guarantee deterministic teardown of open files, database connections, or background threads:

```python
from typing import Iterator
from pytest import fixture
from storage.sqlite_repo import SqliteOrderRepository


@fixture
def order_repo(tmp_path) -> Iterator[SqliteOrderRepository]:
    db_path = tmp_path / "orders.sqlite3"
    repo = SqliteOrderRepository(db_path)
    repo.initialize_schema()
    try:
        yield repo
    finally:
        repo.close()
```

---

## 7. QA & QA-Reviewer Verification Checklist

Before approving a Python test suite or running `lintel lock`, verify every item below:

- [ ] **Contract Alignment:** All imported module paths, class/function names, parameter names, return types, and custom exceptions match `shared/phase-N/contract.md` verbatim.
- [ ] **Exit Code `1` on Stubs:** Running the test group script against the Architect's unimplemented stubs exits with `1` (test failures), never `0` (false pass), `2` (import/syntax/collection error), or `3` (no tests collected).
- [ ] **Exit Code `5 -> 3` Mapping:** Test runner scripts map `pytest` exit code `5` (no tests collected) to Lintel exit code `3`.
- [ ] **Zero Stub-Placeholder Assertions:** No test wraps stub calls in `with raises(NotImplementedError):` or asserts on stub placeholder values.
- [ ] **Comprehensive Boundary Coverage:** `@mark.parametrize` covers `t - 1`, `t`, `t + 1` numeric thresholds, `None`, empty/whitespace strings, empty collections, and invalid inputs raising contracted domain exceptions.
- [ ] **Worktree & Environment Isolation:** All filesystem I/O uses `tmp_path`, environment mutations use `monkeypatch`, clocks and external network calls are deterministically mocked, and `git status` remains clean after test runs.
