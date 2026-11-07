"""From submission to verdict.

Split trust first: tests here, the completion in the sandbox. If the tests need the completion in-process
(they pass functions, read attributes, or are a unittest module with mocks), the tests run in the sandbox and
report one structured record per test; the verdict then carries `Trust.IN_SANDBOX`.
"""

from __future__ import annotations

import textwrap
import time
from typing import Any

from crv.domain.policy import ResourcePolicy
from crv.domain.task import AssertList, CheckFunction, Submission, UnittestModule, expected_unittest_ids
from crv.domain.verdict import Outcome, Trust, Verdict
from crv.sandbox.zygote import Child, ChildCrashed, ChildLoadError, ChildTimeout, ZygoteClient
from crv.verify import trusted


class Verifier:
    def __init__(self, zygote: ZygoteClient) -> None:
        self.zygote = zygote

    def verify(self, sub: Submission, policy: ResourcePolicy) -> Verdict:
        t0 = time.monotonic()
        tests = sub.task.tests
        if isinstance(tests, UnittestModule):
            return self._in_sandbox(sub, tests.source, policy, t0, ())
        try:
            return self._split(sub, tests, policy, t0)
        except trusted.Unsupported as e:
            module = as_unittest(tests)
            return self._in_sandbox(sub, module, policy, t0, (f"fell_back: {e}",))

    # -- split trust -------------------------------------------------------------------------------------

    def _split(self, sub: Submission, tests: CheckFunction | AssertList, policy: ResourcePolicy, t0: float) -> Verdict:
        with self.zygote.spawn(policy) as child:
            loaded = _load(child, sub, Trust.SPLIT, t0)
            if isinstance(loaded, Verdict):
                return loaded
            ns = trusted.namespace(child, loaded)
            try:
                trusted.run(tests, ns)
            except trusted.Unsupported:
                raise
            except trusted.Abort as e:
                return _verdict(Outcome(e.outcome), e.reason, Trust.SPLIT, child, t0, (e.signal,) if e.signal else ())
            except AssertionError as e:
                line = trusted.failing_line()
                reason = f"assertion failed: {line}" if line else f"assertion failed: {e}"
                return _verdict(Outcome.FAILED, reason, Trust.SPLIT, child, t0)
            except Exception as e:
                line = trusted.failing_line()
                where = f" at: {line}" if line else ""
                return _verdict(Outcome.FAILED, f"{type(e).__name__}: {e}{where}"[:500], Trust.SPLIT, child, t0)
            return _verdict(Outcome.PASSED, "all tests passed", Trust.SPLIT, child, t0)

    # -- in-sandbox --------------------------------------------------------------------------------------

    def _in_sandbox(
        self, sub: Submission, module: str, policy: ResourcePolicy, t0: float, signals: tuple[str, ...]
    ) -> Verdict:
        expected = expected_unittest_ids(module)
        with self.zygote.spawn(policy) as child:
            loaded = _load(child, sub, Trust.IN_SANDBOX, t0)
            if isinstance(loaded, Verdict):
                return loaded
            results: dict[str, tuple[str, str]] = {}
            extra: list[str] = list(signals)
            try:
                child.send_tests(module)
                while True:
                    msg = child.receive()
                    if not isinstance(msg, list) or not msg:
                        raise ChildCrashed("protocol violation", kind="protocol")
                    if msg[0] == "test" and len(msg) == 4:
                        test_id, status, detail = str(msg[1]), str(msg[2]), str(msg[3])
                        if test_id in results:
                            extra.append(f"duplicate_report: {test_id}")
                        results.setdefault(test_id, (status, detail))
                    elif msg[0] == "suite_error" and len(msg) == 3:
                        return _verdict(
                            Outcome.FAILED, f"test module failed: {msg[1]}: {msg[2]}", Trust.IN_SANDBOX, child, t0
                        )
                    elif msg[0] == "tests_done" and len(msg) == 2 and isinstance(msg[1], list):
                        changed = [str(x) for x in msg[1]]
                        break
                    else:
                        raise ChildCrashed("protocol violation", kind="protocol")
            except (ChildTimeout, ChildCrashed) as e:
                a = trusted.abort_from(e)
                sig = (*extra, a.signal) if a.signal else tuple(extra)
                return _verdict(Outcome(a.outcome), a.reason, Trust.IN_SANDBOX, child, t0, sig)
            if changed:
                return _verdict(
                    Outcome.FAILED,
                    f"the completion replaced test machinery: {', '.join(changed)}",
                    Trust.IN_SANDBOX,
                    child,
                    t0,
                    (*extra, "tampering"),
                )
            unknown = sorted(set(results) - set(expected))
            if unknown:
                extra.append(f"unexpected_reports: {len(unknown)}")
            missing = [t for t in expected if t not in results]
            if missing or not expected:
                reported = len(expected) - len(missing)
                return _verdict(
                    Outcome.FAILED,
                    f"{reported} of {len(expected)} tests reported a result",
                    Trust.IN_SANDBOX,
                    child,
                    t0,
                    (*extra, "missing_tests"),
                )
            for t in expected:
                status, detail = results[t]
                if status != "pass":
                    return _verdict(
                        Outcome.FAILED, f"{t} {status}: {detail}", Trust.IN_SANDBOX, child, t0, tuple(extra)
                    )
            return _verdict(
                Outcome.PASSED, f"all {len(expected)} tests passed", Trust.IN_SANDBOX, child, t0, tuple(extra)
            )


def _load(child: Child, sub: Submission, trust: Trust, t0: float) -> dict[str, Any] | Verdict:
    try:
        return child.load(sub.solution_source)
    except ChildLoadError as e:
        return _verdict(Outcome.FAILED, f"the completion raised {e.type_name} at import: {e.message}", trust, child, t0)
    except (ChildTimeout, ChildCrashed) as e:
        a = trusted.abort_from(e)
        reason = f"{a.reason} (before any test ran)"
        return _verdict(Outcome(a.outcome), reason, trust, child, t0, (a.signal,) if a.signal else ())


def _verdict(
    outcome: Outcome, reason: str, trust: Trust, child: Child, t0: float, signals: tuple[str, ...] = ()
) -> Verdict:
    return Verdict(
        outcome=outcome,
        reason=reason,
        trust=trust,
        calls=child.calls,
        wall_ms=round((time.monotonic() - t0) * 1000, 2),
        signals=signals,
    )


def as_unittest(tests: CheckFunction | AssertList) -> str:
    """The same tests as a unittest module, for in-sandbox mode."""
    if isinstance(tests, CheckFunction):
        return (
            f"{tests.source}\n\nimport unittest\n"
            f"class CRVCheck(unittest.TestCase):\n    def test_check(self):\n        check({tests.entry_point})\n"
        )
    body = textwrap.indent("\n".join(tests.asserts) or "pass", " " * 8)
    return (
        f"{tests.setup}\n\nimport unittest\n"
        f"class CRVAsserts(unittest.TestCase):\n    def test_asserts(self):\n{body}\n"
    )
