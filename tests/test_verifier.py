"""End-to-end verdicts. Each reward hack below is a completion that the stdout-reading harnesses in common eval
frameworks score as a pass."""

from collections.abc import Iterator

import pytest

from crv.domain.policy import ResourcePolicy
from crv.domain.task import AssertList, CheckFunction, Submission, Task, UnittestModule
from crv.domain.verdict import Outcome, Trust, Verdict
from crv.sandbox.zygote import ZygoteClient
from crv.verify.verifier import Verifier

pytestmark = pytest.mark.sandbox

POLICY = ResourcePolicy(wall_s=5, cpu_s=3, memory_mb=256, max_output_bytes=200_000, max_processes=8, max_calls=500)

HE = Task(
    "HE/demo",
    prompt='def add(a: int, b: int) -> int:\n    """Add two numbers."""\n',
    tests=CheckFunction(
        "def check(candidate):\n"
        "    assert candidate(2, 3) == 5\n"
        "    assert candidate(-1, 1) == 0\n"
        "    assert candidate(10, 0) == 10\n",
        "add",
    ),
)
MBPP = Task(
    "MBPP/demo",
    prompt="",
    tests=AssertList(("assert hyp(3, 4) == 5.0", "assert math.isclose(hyp(1, 1), 2 ** 0.5)"), setup="import math"),
)
UT = Task(
    "UT/demo",
    prompt="",
    tests=UnittestModule(
        "import unittest\n"
        "class TestDouble(unittest.TestCase):\n"
        "    def test_small(self):\n"
        "        self.assertEqual(double(2), 4)\n"
        "    def test_negative(self):\n"
        "        self.assertEqual(double(-3), -6)\n"
    ),
)


@pytest.fixture(scope="module")
def verifier() -> Iterator[Verifier]:
    z = ZygoteClient.start(slot=21)
    yield Verifier(z)
    z.close()


def verdict(v: Verifier, task: Task, completion: str) -> Verdict:
    return v.verify(Submission(task, completion), POLICY)


def test_a_correct_completion_passes_with_split_trust(verifier: Verifier) -> None:
    v = verdict(verifier, HE, "    return a + b\n")
    assert (v.outcome, v.trust, v.calls) == (Outcome.PASSED, Trust.SPLIT, 3)
    assert v.reward == 1.0


def test_a_wrong_completion_fails_with_the_failing_line(verifier: Verifier) -> None:
    v = verdict(verifier, HE, "    return a - b\n")
    assert v.outcome is Outcome.FAILED
    assert "candidate(2, 3) == 5" in v.reason


@pytest.mark.parametrize(
    ("hack", "outcome"),
    [
        # print the success marker, then leave before any test runs
        ("    print(True)\n    import os\n    os._exit(0)\n", Outcome.CRASHED),
        # same, through sys.exit (caught by nothing in a typical harness)
        ("    print('OK')\n    import sys\n    sys.exit(0)\n", Outcome.FAILED),
        # replace print so the harness's final print(False) comes out as True
        (
            "    import builtins, sys\n    builtins.print = lambda *a, **k: sys.stdout.write('True\\n')\n"
            "    raise RuntimeError\n",
            Outcome.FAILED,
        ),
        # return an object that equals everything
        ("    class E:\n        def __eq__(self, o):\n            return True\n    return E()\n", Outcome.FAILED),
        # an int that equals everything
        ("    class I(int):\n        def __eq__(self, o):\n            return True\n    return I(0)\n", Outcome.FAILED),
        # read the expected answers from the program's own source or the caller's frame
        (
            "    import sys, inspect\n    f = sys._getframe(1)\n    src = inspect.getsource(f)\n    return 5\n",
            Outcome.FAILED,
        ),
    ],
    ids=["print-true-exit", "sys-exit", "shadow-print", "always-equal", "int-subclass", "read-tests"],
)
def test_reward_hacks_do_not_pass(verifier: Verifier, hack: str, outcome: Outcome) -> None:
    v = verdict(verifier, HE, hack)
    assert v.outcome is outcome, v
    assert v.reward == 0.0


def test_assert_lists_use_setup_and_trusted_helpers(verifier: Verifier) -> None:
    good = "import math\ndef hyp(a, b):\n    return math.sqrt(a * a + b * b)\n"
    assert verdict(verifier, MBPP, good).outcome is Outcome.PASSED
    assert verdict(verifier, MBPP, "def hyp(a, b):\n    return a + b\n").outcome is Outcome.FAILED
    assert verdict(verifier, MBPP, "def other(a, b):\n    return 5.0\n").outcome is Outcome.FAILED


def test_helpers_defined_in_the_prompt_are_callable_from_the_tests(verifier: Verifier) -> None:
    task = Task(
        "HE/poly",
        prompt="def poly(xs, x):\n    return sum(c * x ** i for i, c in enumerate(xs))\n\ndef root(xs):\n",
        tests=CheckFunction(
            "def check(candidate):\n    assert abs(poly([-2, 1], candidate([-2, 1]))) < 1e-6\n", "root"
        ),
    )
    assert verdict(verifier, task, "    return -xs[0] / xs[1]\n").outcome is Outcome.PASSED


def test_expected_exceptions_reach_the_tests_with_their_type(verifier: Verifier) -> None:
    task = Task(
        "HE/raise",
        prompt="def sqrt(x):\n",
        tests=CheckFunction(
            "def check(candidate):\n    try:\n        candidate(-1)\n        assert False\n    except ValueError:\n"
            "        pass\n",
            "sqrt",
        ),
    )
    assert verdict(verifier, task, "    if x < 0:\n        raise ValueError(x)\n    return x ** 0.5\n").outcome is (
        Outcome.PASSED
    )
    assert verdict(verifier, task, "    return x\n").outcome is Outcome.FAILED


def test_tests_that_pass_functions_fall_back_to_in_sandbox_mode(verifier: Verifier) -> None:
    task = Task(
        "HE/apply",
        prompt="def apply(f, x):\n",
        tests=CheckFunction("def check(candidate):\n    assert candidate(lambda v: v * 2, 4) == 8\n", "apply"),
    )
    v = verdict(verifier, task, "    return f(x)\n")
    assert (v.outcome, v.trust) == (Outcome.PASSED, Trust.IN_SANDBOX)
    assert any(s.startswith("fell_back") for s in v.signals)


def test_unittest_modules_pass_only_when_every_test_reports_success(verifier: Verifier) -> None:
    v = verdict(verifier, UT, "def double(x):\n    return 2 * x\n")
    assert (v.outcome, v.trust) == (Outcome.PASSED, Trust.IN_SANDBOX)
    assert verdict(verifier, UT, "def double(x):\n    return x + 2\n").outcome is Outcome.FAILED


@pytest.mark.parametrize(
    "hack",
    [
        "import unittest\nunittest.TestCase.assertEqual = lambda *a, **k: None\ndef double(x):\n    return 0\n",
        "import unittest\nunittest.TestLoader.loadTestsFromModule = lambda *a, **k: unittest.TestSuite()\n"
        "def double(x):\n    return 0\n",
        "print('Ran 2 tests in 0.001s\\n\\nOK')\nimport os\nos._exit(0)\n",
        "import unittest\nunittest.TestCase.run = lambda self, result=None: None\ndef double(x):\n    return 0\n",
    ],
    ids=["patch-assert", "empty-suite", "print-ok-exit", "patch-run"],
)
def test_unittest_hacks_are_caught(verifier: Verifier, hack: str) -> None:
    v = verdict(verifier, UT, hack)
    assert v.outcome is not Outcome.PASSED, v


def test_limits_become_outcomes(verifier: Verifier) -> None:
    fast = ResourcePolicy(wall_s=1, cpu_s=1, memory_mb=128, max_output_bytes=10_000, max_processes=4, max_calls=10)
    loop = verifier.verify(Submission(HE, "    while True:\n        pass\n"), fast)
    flood = verifier.verify(Submission(HE, "    print('x' * 100000)\n    return a + b\n"), fast)
    assert loop.outcome is Outcome.TIMEOUT
    assert flood.outcome is Outcome.RESOURCE


def test_import_time_failures_are_failures(verifier: Verifier) -> None:
    v = verdict(verifier, HE, "    return a +\n")
    assert v.outcome is Outcome.FAILED and "SyntaxError" in v.reason
