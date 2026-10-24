---
name: python-implementation
description: Idiomatic Python coding standards and implementation practices for Lintel DEV and DEV-Reviewer
---
# Python Implementation

Coding standards, safety invariants, and implementation practices for DEV and DEV-Reviewer in Python projects.

## 1. Contract & Lane Discipline
- **Exact Contract Adherence:** Implement the exact module paths, exported symbol names, function/method signatures, keyword/positional argument semantics, default values, return types, and exception contracts defined in `shared/phase-N/contract.md`.
- **Strict Lane Separation:** DEV may only modify production code files. Never edit, create, or delete files inside test directories (`tests/`, `e2e/`) or Lintel scripts (`.lintel/scripts/`). Never read QA's locked test files during implementation—implement strictly from `requirements.md`, `design.md`, and `contract.md`.
- **Package & Export Hygiene:** Ensure all public symbols specified in `contract.md` are imported or defined at the exact module path expected by callers, and maintain clean `__all__` declarations where package-level exports are contracted.

## 2. PEP 8, Type Annotations & Data Modeling
- **PEP 8 Compliance:** Follow PEP 8 formatting and naming conventions strictly: `snake_case` for modules, functions, methods, and variables; `CapWords` (`PascalCase`) for classes and exception types; `UPPER_SNAKE_CASE` for module-level constants.
- **Full Type Annotations:** Annotate all function and method parameters and return values using `typing` and `collections.abc` (`Sequence`, `Mapping`, `Iterable`, `Callable`, `Optional`, `Union`, `Any`). Prefer abstract collection types (`Sequence`, `Mapping`) for input parameters and concrete types (`list`, `dict`, `tuple`, `set`) for return values.
- **Immutable Data Structures:** Model value objects, DTOs, and structured results using `@dataclass(frozen=True)` (and `slots=True` where supported by the target Python version) or `typing.NamedTuple` to prevent accidental state mutation across boundaries.
- **No Mutable Default Arguments:** Never use mutable literals (`[]`, `{}`, `set()`) as default parameter values in function signatures or dataclass fields. Use `None` with initialization inside the function body (`items = list(items) if items is not None else []`) or `field(default_factory=list)` in dataclasses.

## 3. Resource Management, Error Handling & Determinism
- **Deterministic Resource Cleanup:** Always use context managers (`with` statements) for files (`pathlib.Path.open`, `open(..., encoding="utf-8")`), network sockets, database connections, locks, temporary directories (`tempfile.TemporaryDirectory`), and subprocesses. Always specify explicit `encoding="utf-8"` on text I/O.
- **Subprocess Safety:** Invoke external processes via `subprocess.run([...], check=..., capture_output=True, text=True, timeout=...)` with an argument list (`shell=False`). Never pass untrusted input into `shell=True`.
- **Explicit Exception Chaining:** Catch specific exception types rather than bare `except:` or broad `except Exception:` (unless at a top-level boundary). When translating lower-level errors into domain exceptions specified by `contract.md`, preserve causal tracebacks using explicit chaining: `raise DomainError("descriptive context") from exc`.
- **Timezone-Aware Datetimes:** Never use naive datetimes (`datetime.now()`, `datetime.utcnow()`). Always construct and compare timezone-aware UTC timestamps using `datetime.now(timezone.utc)`. Pay strict attention to whether `contract.md` specifies Unix epoch seconds (`float` / `int`) or ISO-8601 strings.
- **Numeric & Boundary Validation:** Guard against `math.nan`, `math.inf`, boolean-as-int pitfalls (`isinstance(val, int) and not isinstance(val, bool)`), and floating-point precision issues (use `decimal.Decimal` where exact monetary or fixed-point arithmetic is required).

## 4. Worktree Hygiene & Gate Verification
- **Worktree & Bytecode Hygiene:** Keep the working tree clean of stray artifacts, debug dumps, or untracked temporary files. Respect `PYTHONDONTWRITEBYTECODE=1` (`python3 -B`) so `.pyc` files and `__pycache__/` directories do not pollute untracked file checks or isolated check worktrees. Do not leave `print()` debug statements or `breakpoint()` calls in production code; use the `logging` module.
- **Dependencies:** Declare dependencies exclusively in `pyproject.toml` or `requirements.txt` as configured by the repository. Never install unrecorded packages ad-hoc during implementation.
- **Mandatory Gate Verification Before `done`:**
  1. Run `./.lintel/scripts/build.sh` to verify syntax, bytecode compilation, static type checks, and linters pass.
  2. Run `./.lintel/bin/lintel check --phase <N>` to execute the full test gate against your candidate tree in an isolated worktree.
  3. Only call `./.lintel/bin/lintel done --verdict complete` after `lintel check --phase <N>` records a green result for your current working tree.
