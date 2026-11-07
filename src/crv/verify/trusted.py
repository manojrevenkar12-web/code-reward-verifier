"""The trusted side of split-trust verification.

The tests run here, in the verifier's own process. Every name the completion defines becomes a `Proxy`;
calling it sends the arguments to the sandboxed child and returns the decoded result. The completion
therefore only ever sees inputs. It never sees the test code, the expected values or the harness, so it
cannot print its way to a pass, read the answers, or swap in an object that equals everything (such a value
does not decode).
"""

from __future__ import annotations

import builtins
import importlib
import linecache
import sys
from typing import Any

from crv.domain.task import AssertList, CheckFunction
from crv.domain.values import EncodeError, decode
from crv.sandbox.zygote import Child, ChildCrashed, ChildRaised, ChildTimeout


class Abort(BaseException):
    """Ends a test run with a fixed outcome. A BaseException, so `except Exception` in test code cannot swallow
    a crash or a timeout of the completion."""

    def __init__(self, outcome: str, reason: str, signal: str | None = None) -> None:
        super().__init__(reason)
        self.outcome = outcome
        self.reason = reason
        self.signal = signal


class Unsupported(BaseException):
    """The tests do something a proxy cannot carry (pass a function, read an attribute): run in-sandbox."""


class CandidateError(Exception):
    """An exception of a type the completion defined itself."""


def exception_from(e: ChildRaised) -> BaseException:
    if e.type_name == "NonDataResult":
        return Abort("failed", f"returned a value that is not plain data ({e.message})", "non_data_result")
    if e.type_name in ("SystemExit", "KeyboardInterrupt", "GeneratorExit"):
        return Abort("failed", f"the completion raised {e.type_name}", "exit_attempt")
    cls = getattr(builtins, e.type_name, None)
    if isinstance(cls, type) and issubclass(cls, Exception):
        try:
            return cls(e.message)
        except Exception:
            return CandidateError(f"{e.type_name}: {e.message}")
    return CandidateError(f"{e.type_name}: {e.message}")


def abort_from(e: ChildTimeout | ChildCrashed) -> Abort:
    if isinstance(e, ChildTimeout):
        return Abort("timeout", str(e))
    if e.kind == "resource":
        return Abort("resource", str(e))
    return Abort("crashed", str(e), "protocol_violation" if e.kind == "protocol" else "early_exit")


class Proxy:
    """Stands in for a function the completion defined."""

    def __init__(self, name: str, child: Child) -> None:
        self._name = name
        self._child = child

    @property
    def __name__(self) -> str:
        return self._name

    def __call__(self, *args: Any, **kwargs: Any) -> object:
        try:
            self._child.send_call(self._name, list(args), kwargs)
        except EncodeError:
            raise Unsupported(f"the tests pass a non-data argument to {self._name}") from None
        except (ChildTimeout, ChildCrashed) as e:
            raise abort_from(e) from None
        try:
            return self._child.receive_result()
        except ChildRaised as e:
            raise exception_from(e) from None
        except (ChildTimeout, ChildCrashed) as e:
            raise abort_from(e) from None

    def __getattr__(self, attr: str) -> Any:
        raise Unsupported(f"the tests read {self._name}.{attr}")

    def __repr__(self) -> str:
        return f"<completion function {self._name}>"


def namespace(child: Child, names: dict[str, Any]) -> dict[str, Any]:
    ns: dict[str, Any] = {"__name__": "__crv_tests__", "__builtins__": builtins}
    for name, desc in names.items():
        if not isinstance(name, str) or not isinstance(desc, list) or not desc:
            continue
        if desc[0] == "callable":
            ns[name] = Proxy(name, child)
        elif desc[0] == "module" and len(desc) == 2 and isinstance(desc[1], str):
            # Only standard-library modules, imported here by their real name: the trusted side gets its own
            # copy, never the completion's.
            if desc[1].split(".")[0] in sys.stdlib_module_names:
                try:
                    ns[name] = importlib.import_module(desc[1])
                except ImportError:
                    continue
        elif desc[0] == "data" and len(desc) == 2 and isinstance(desc[1], str):
            try:
                ns[name] = decode(desc[1])
            except Exception:
                continue
    return ns


def run(tests: CheckFunction | AssertList, ns: dict[str, Any]) -> None:
    """Run trusted tests; returns normally on a pass, raises on anything else."""
    if isinstance(tests, CheckFunction):
        _exec(tests.source, "<tests>", ns)
        candidate = ns.get(tests.entry_point)
        if not isinstance(candidate, Proxy):
            raise Abort("failed", f"the completion does not define {tests.entry_point}")
        ns["check"](candidate)
    else:
        if tests.setup:
            _exec(tests.setup, "<setup>", ns)
        for i, line in enumerate(tests.asserts):
            _exec(line, f"<test {i}>", ns)


def _exec(source: str, filename: str, ns: dict[str, Any]) -> None:
    # Register the source so tracebacks (and our failure reasons) can quote the failing line.
    linecache.cache[filename] = (len(source), None, source.splitlines(keepends=True), filename)
    exec(compile(source, filename, "exec"), ns)


def failing_line() -> str:
    """The test line that raised, from the innermost frame in test code."""
    tb = sys.exc_info()[2]
    line = ""
    while tb is not None:
        code = tb.tb_frame.f_code
        if code.co_filename.startswith("<test") or code.co_filename == "<setup>":
            line = linecache.getline(code.co_filename, tb.tb_lineno).strip()
        tb = tb.tb_next
    return line
