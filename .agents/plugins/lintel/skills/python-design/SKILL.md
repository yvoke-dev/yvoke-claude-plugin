---
name: python-design
description: Architectural and design patterns in Python
---
# Python Design

Architectural and design practices for Python projects under Lintel (`architect` and `architect-reviewer`).

## 1. Type-First Contracts (`shared/phase-N/contract.md`)
- **Explicit Module & Symbol Signatures:** Specify exact module paths (e.g., `src/pkg/domain.py`), public function and method signatures, class names, parameter types, default values, and return types in `contract.md`. QA and DEV treat `contract.md` as their single source of truth.
- **Structured Data Models:** Prefer `@dataclass(frozen=True, slots=True)` or `NamedTuple` for immutable value objects and domain carriers, `TypedDict` for structured dictionary payloads (JSON/config shapes), and `Enum` / `StrEnum` for closed sets of states or modes instead of untyped `dict[str, Any]` or positional tuples.
- **Structural Subtyping with `Protocol`:** Define boundary interfaces using `typing.Protocol` (rather than deep abstract inheritance trees) when decoupling domain services from storage, network, or clock adapters.
- **Complete Type Annotations:** Annotate all public function parameters, return values (including `-> None`), and class attributes using modern standard typing (`list[T]`, `dict[K, V]`, `X | None`, `Sequence[T]`, `Mapping[K, V]`, `Callable[...]` from `collections.abc`).
- **Custom Exception Hierarchies:** Define domain-specific exceptions rooted in a package base error (e.g., `class DomainError(Exception): ...`) and document the exact exception types raised on invalid inputs or failure states in `contract.md`. Never rely on bare `Exception` or undocumented `ValueError`/`KeyError` leaks across module boundaries.

## 2. Lintel Stub Files (`## Stub files` in `design.md`)
- **Import & Parse Cleanly (`exit 1`, never `exit 2`):** Every stub module listed under `## Stub files` in `design.md` must exist on disk, contain valid Python syntax, resolve all imports without `ImportError` or circular import failures, and expose every class, data structure, exception, and function signature declared in `contract.md`.
- **Fail at Runtime with `NotImplementedError`:** Stub functions and methods must raise `NotImplementedError("Not implemented")` so that during `lintel lock`, QA's test suite imports the module and collects all test cases cleanly, failing with test execution/assertion failures (`exit 1`) rather than collection, syntax, or import errors (`exit 2`).
- **Define Data Carriers & Exceptions Fully in Stubs:** Data classes (`@dataclass(frozen=True)`), `NamedTuple`, `TypedDict`, `Enum`, `Protocol` definitions, and custom exception classes have no behavioral body to defer—define their fields and inheritance completely in the stub so QA can instantiate them directly in test cases.
- **Preserve Existing Public APIs:** When extending an existing module for a new phase, never delete or break existing functions, classes, or constants—all baseline tests in the `tests/` directory must remain green during `lintel lock`.

## 3. Modular Architecture, Pure Core & Deterministic Boundaries
- **Functional Core, Imperative Shell:** Isolate pure domain logic, parsing, validation, state transitions, and calculations in cohesive modules free of filesystem, network, subprocess, database, or framework (`FastAPI`, `Flask`, `Django`, `Click`) side effects. Keep I/O orchestration at thin outer boundary layers.
- **Deterministic Clock & Dependency Injection:** For time-sensitive, random, or external-state logic (token expiration, rate limiting, retry backoff, UUID/nonce generation), design signatures with explicit optional parameters or injectable callables (e.g., `now: datetime | None = None` or `clock: Callable[[], float] = time.monotonic`) so unit tests under `tests/` are 100% deterministic without monkeypatching or `time.sleep`.
- **Acyclic Package Structure:** Keep module dependencies strictly directional (`adapters -> services -> domain -> models/errors`). Avoid importing upward from subpackages into parent modules, and keep package initialization files minimal to prevent circular imports.

## 4. Test Outline Mapping (`## Test outline` in `design.md`)
- **`unit`:** Map all pure domain functions, data model invariants, boundary values, malformed inputs, custom exception branches, and injected-clock/stubbed-protocol behaviors to `unit`.
- **`integration`:** Map multi-module workflows, database repositories, filesystem persistence, CLI entrypoints, or HTTP route handlers (`TestClient` / ASGI / WSGI) to `integration`. If a phase only modifies pure domain logic with no I/O or multi-component wiring, explicitly document `- integration: none: <clear justification>`.
- **`e2e`:** Map full application lifecycle flows, live server/process invocations, or end-to-end user journeys to `e2e`. If a phase adds an internal library or pure module with no end-to-end entrypoint changes, explicitly document `- e2e: none: Pure Python module with no CLI/HTTP entrypoint changes; fully covered by unit tests`.

