#!/usr/bin/env python3
"""Python interpreter discovery and launcher support (M1a-34, PLAN §3.1, §3.5).

stdlib only, Python 3.9+ compatible.

Discovers a working python3 >= 3.9 interpreter following PLAN §3.1 candidate order:
  1. Running interpreter (sys.executable or base interpreter if in virtualenv)
  2. /opt/homebrew/bin/python3
  3. /usr/local/bin/python3
  4. /Library/Frameworks/Python.framework/Versions/Current/bin/python3
  5. PATH directories (and ~/.local/bin) for python3.13 ... python3.9, python3
  6. pyenv versions (~/.pyenv/versions/*/bin/python3), newest first numerically
  7. uv python versions (~/.local/share/uv/python/*/bin/python3), newest first numerically
  8. Developer directory python on macOS (@DEVPY), discovered via `xcode-select -p`

Filters:
  - Relative paths skipped (must start with /)
  - Shims skipped (any path with /shims/ in it)
  - On macOS: /usr/bin/python3 stub never executed, replaced by developer directory python

Test hooks (honoured only when FIND_PYTHON_SELFTEST=1):
  FIND_PYTHON_OS, FIND_PYTHON_STUB, FIND_PYTHON_XCSELECT, FIND_PYTHON_NO_FIXED.
"""

import argparse
import glob
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from typing import Callable, Iterator, List, Mapping, Optional, Sequence, Tuple, Union

SNIPPET = (
    "import os, sys; assert sys.version_info >= (3, 9); "
    "assert sys.prefix == getattr(sys, 'base_prefix', sys.prefix); "
    "print(os.path.realpath(sys.executable))"
)
EXTRA_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def run_bounded(argv: Sequence[str], timeout: int) -> Tuple[Optional[int], str]:
    """Run argv in its own process group; return (returncode, stdout) or (None, '') on timeout.

    Output goes to a temporary file, not a pipe, so a grandchild that keeps running cannot block us;
    on timeout the whole group is killed.
    """
    with tempfile.TemporaryFile() as out:
        try:
            proc = subprocess.Popen(
                list(argv),
                stdout=out,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            return 127, ""
        try:
            proc.wait(timeout=timeout)
        except BaseException as exc:  # timeout, or SIGTERM/Ctrl-C to us: take the group down
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                proc.wait()
            except BaseException:
                pass
            if isinstance(exc, subprocess.TimeoutExpired):
                return None, ""
            raise
        out.seek(0)
        return proc.returncode, out.read().decode("utf-8", "replace")


def developer_python(xcselect: str, timeout: int = 5) -> Optional[str]:
    """Find python3 in the active developer directory, or None. Never runs /usr/bin/python3."""
    if not os.access(xcselect, os.X_OK):
        return None
    rc, out = run_bounded([xcselect, "-p"], timeout)  # -p only prints; DEVELOPER_DIR is honoured
    d = out.strip().splitlines()[0].strip() if out.strip() else ""
    if rc != 0 or not d:
        return None
    for p in (d + "/usr/bin/python3", d + "/Contents/Developer/usr/bin/python3"):
        if os.access(p, os.X_OK) and not os.path.isdir(p):
            return p
    return None


def newest_first(paths: Sequence[str]) -> List[str]:
    """Sort <root>/<name>/bin/python3 paths by the version in <name>, newest first (numerically)."""
    def key(path: str) -> Tuple[int, int, int]:
        parts = path.split("/")
        if len(parts) >= 3:
            name = parts[-3]
        else:
            name = path
        name = re.sub(r"^cpython-", "", name)
        m = re.match(r"[0-9]+(\.[0-9]+)*", name)
        if m:
            nums = [int(x) for x in m.group(0).split(".")]
        else:
            nums = [0]
        return (nums + [0, 0, 0])[:3]  # type: ignore[return-value]

    return sorted(paths, key=key, reverse=True)


def get_running_interpreter() -> str:
    """Return sys.executable, or the base interpreter if in a virtualenv."""
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        base_exe = getattr(sys, "_base_executable", None)
        if base_exe and os.path.exists(base_exe) and base_exe != sys.executable:
            return base_exe
        base_cand = os.path.join(sys.base_prefix, "bin", "python3")
        if os.path.exists(base_cand):
            return base_cand
    return sys.executable


def candidate_list(
    explicit: Sequence[str] = (),
    only: bool = False,
    os_name: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    no_self: bool = False,
) -> Iterator[str]:
    """Generate candidate interpreter paths in priority order per PLAN §3.1."""
    env = os.environ if env is None else env
    hooks = env if env.get("FIND_PYTHON_SELFTEST") == "1" else {}
    os_name = os_name or hooks.get("FIND_PYTHON_OS") or os.uname().sysname

    # 1. Explicit candidate paths (if any)
    yield from explicit

    if only:
        return

    # 2. Running interpreter (sys.executable or base interpreter if in venv)
    if not no_self:
        yield get_running_interpreter()

    # 3. Fixed standard locations (skipped if self-test hook FIND_PYTHON_NO_FIXED is set)
    if not (env.get("FIND_PYTHON_SELFTEST") == "1" and env.get("FIND_PYTHON_NO_FIXED")):
        yield "/opt/homebrew/bin/python3"
        yield "/usr/local/bin/python3"
        yield "/Library/Frameworks/Python.framework/Versions/Current/bin/python3"

    # 4. PATH directories (and ~/.local/bin)
    dirs = [d for d in env.get("PATH", "").split(":") if d]
    if not (env.get("FIND_PYTHON_SELFTEST") == "1" and env.get("FIND_PYTHON_NO_FIXED")):
        dirs.extend(EXTRA_DIRS)
    home = env.get("HOME")
    if home:
        dirs.append(home + "/.local/bin")
    for name in ("python3.13", "python3.12", "python3.11", "python3.10", "python3.9", "python3"):
        for d in dirs:
            yield d + "/" + name

    # 5. pyenv versions (~/.pyenv/versions/*/bin/python3)
    pyenv = env.get("PYENV_ROOT") or (home or "") + "/.pyenv"
    yield from newest_first(glob.glob(pyenv + "/versions/*/bin/python3"))

    # 6. uv python versions (~/.local/share/uv/python/*/bin/python3)
    # Minor-version directories (cpython-3.13-*) are links that follow uv upgrades; the patch
    # directories (cpython-3.13.14-*) go away when uv replaces them, so they come after.
    uv = env.get("UV_PYTHON_INSTALL_DIR") or (home or "") + "/.local/share/uv/python"
    yield from newest_first(glob.glob(uv + "/cpython-?.?-*/bin/python3")
                            + glob.glob(uv + "/cpython-?.??-*/bin/python3"))
    yield from newest_first(glob.glob(uv + "/cpython-*/bin/python3"))

    # 7. Developer directory python on macOS (@DEVPY)
    if os_name == "Darwin":
        yield "@DEVPY"


def find_python(
    candidates: Sequence[str] = (),
    only: bool = False,
    real: bool = False,
    timeout: int = 5,
    budget: int = 30,
    verbose: bool = False,
    env: Optional[Mapping[str, str]] = None,
    os_name: Optional[str] = None,
    stub: Optional[str] = None,
    xcselect: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
    no_self: bool = False,
) -> Optional[str]:
    """Find a working Python >= 3.9 interpreter from candidate list or standard locations.

    Returns the absolute candidate path as found (or realpath if real=True), or None.
    """
    env = os.environ if env is None else env
    hooks = env if env.get("FIND_PYTHON_SELFTEST") == "1" else {}
    os_name = os_name or hooks.get("FIND_PYTHON_OS") or os.uname().sysname
    stub = stub or hooks.get("FIND_PYTHON_STUB") or "/usr/bin/python3"
    xcselect = xcselect or hooks.get("FIND_PYTHON_XCSELECT") or "/usr/bin/xcode-select"

    def say(msg: str) -> None:
        if verbose:
            (log or (lambda m: print("find-python: " + m, file=sys.stderr)))(msg)

    started = time.monotonic()
    seen = set()
    for c in candidate_list(
        explicit=list(candidates),
        only=only,
        os_name=os_name,
        env=env,
        no_self=no_self,
    ):
        if c == "@DEVPY":
            c = developer_python(xcselect)
            if not c:
                continue
        if not c.startswith("/") or "/shims/" in c:
            say("skip (relative or version-manager shim): %s" % c)
            continue
        if not os.access(c, os.X_OK) or os.path.isdir(c):
            continue
        if os_name == "Darwin" and (
            c == "/usr/bin/python3" or (os.path.exists(stub) and os.path.samefile(c, stub))
        ):
            dev = developer_python(xcselect)
            if not dev:
                say("skip %s: the xcode-select stub and no developer tools (never executed)" % c)
                continue
            say("%s is the xcode-select stub; using %s instead" % (c, dev))
            c = dev
        if c in seen:
            continue
        seen.add(c)
        if time.monotonic() - started >= budget:
            say("time budget used up before %s" % c)
            continue
        rc, out = run_bounded([c, "-B", "-c", SNIPPET], timeout)
        if rc is None:
            say("FAIL %s: no answer in %ss (hung), killed" % (c, timeout))
        elif rc != 0:
            say("FAIL %s: exit %s (broken, too old, or a venv)" % (c, rc))
        elif not out.strip():
            say("FAIL %s: exit 0 but printed nothing" % c)
        else:
            realpath = out.strip().splitlines()[0]
            say("OK %s (realpath %s)" % (c, realpath))
            return realpath if real else c
    return None


def find_and_record_python(
    state_dir: Union[str, Path],
    candidates: Sequence[str] = (),
    only: bool = False,
    real: bool = False,
    timeout: int = 5,
    budget: int = 30,
    verbose: bool = False,
    env: Optional[Mapping[str, str]] = None,
    os_name: Optional[str] = None,
    stub: Optional[str] = None,
    xcselect: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
    no_self: bool = False,
) -> Optional[str]:
    """Find a Python >=3.9 interpreter and atomically record its path to <state_dir>/python."""
    found = find_python(
        candidates=candidates,
        only=only,
        real=real,
        timeout=timeout,
        budget=budget,
        verbose=verbose,
        env=env,
        os_name=os_name,
        stub=stub,
        xcselect=xcselect,
        log=log,
        no_self=no_self,
    )
    if found is not None:
        state_path = Path(state_dir).resolve()
        state_path.mkdir(parents=True, exist_ok=True)
        target = state_path / "python"
        temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            dir=state_path,
            prefix=".python.tmp-",
            delete=False,
            encoding="utf-8",
        )
        try:
            temp_file.write(f"{found}\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_file.close()
            os.replace(temp_file.name, target)
        except Exception:
            if os.path.exists(temp_file.name):
                try:
                    os.unlink(temp_file.name)
                except OSError:
                    pass
            raise
    return found


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point for find_python."""
    ap = argparse.ArgumentParser(description="Find a working python3 >= 3.9 interpreter.")
    ap.add_argument("-v", action="store_true", dest="verbose", help="verbose diagnostics to stderr")
    ap.add_argument("--real", action="store_true", help="output realpath of interpreter")
    ap.add_argument("--only", action="store_true", help="try only the --candidate paths")
    ap.add_argument("--list", action="store_true", help="print the candidates in order and exit")
    ap.add_argument("--no-self", action="store_true", help="do not try sys.executable first")
    ap.add_argument("--timeout", type=int, default=5, help="timeout per candidate in seconds")
    ap.add_argument("--budget", type=int, default=30, help="overall search budget in seconds")
    ap.add_argument("--candidate", action="append", default=[], help="candidate to try first (repeatable)")
    a = ap.parse_args(argv)

    if a.list:
        env = os.environ
        hooks = env if env.get("FIND_PYTHON_SELFTEST") == "1" else {}
        os_name = hooks.get("FIND_PYTHON_OS") or os.uname().sysname
        for c in candidate_list(
            explicit=a.candidate,
            only=a.only,
            os_name=os_name,
            env=env,
            no_self=a.no_self,
        ):
            print(c)
        return 0

    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, lambda *_: sys.exit(1))
        except (ValueError, OSError):
            pass

    result = find_python(
        candidates=a.candidate,
        only=a.only,
        real=a.real,
        timeout=a.timeout,
        budget=a.budget,
        verbose=a.verbose,
        no_self=a.no_self,
    )
    if result is None:
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
