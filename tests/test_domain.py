from dataclasses import FrozenInstanceError

import pytest

from crv.domain.policy import ResourcePolicy, load_policies
from crv.domain.task import AssertList, CheckFunction, Task, UnittestModule, expected_unittest_ids
from crv.domain.verdict import Outcome, Trust, Verdict


def test_only_a_pass_earns_reward_and_infra_errors_earn_none() -> None:
    def v(o: Outcome) -> Verdict:
        return Verdict(outcome=o, reason="", trust=Trust.SPLIT)

    assert v(Outcome.PASSED).reward == 1.0
    for o in (Outcome.FAILED, Outcome.TIMEOUT, Outcome.RESOURCE, Outcome.CRASHED):
        assert v(o).reward == 0.0
    # An infrastructure failure says nothing about the completion: no reward, not a zero.
    assert v(Outcome.INFRA_ERROR).reward is None


def test_verdicts_are_immutable_and_serialise() -> None:
    v = Verdict(Outcome.FAILED, "assert failed", Trust.SPLIT, calls=3, wall_ms=1.5, signals=("early_exit",))
    with pytest.raises(FrozenInstanceError):
        v.outcome = Outcome.PASSED  # type: ignore[misc]
    assert v.to_dict() == {
        "outcome": "failed",
        "reward": 0.0,
        "reason": "assert failed",
        "trust": "split",
        "calls": 3,
        "wall_ms": 1.5,
        "signals": ["early_exit"],
    }


def test_policies_come_from_config_per_lane() -> None:
    policies = load_policies()
    assert set(policies) >= {"rl", "eval"}
    rl = policies["rl"]
    assert isinstance(rl, ResourcePolicy)
    assert 0 < rl.cpu_s <= rl.wall_s
    assert rl.memory_mb >= 64


def test_policy_rejects_nonsense() -> None:
    with pytest.raises(ValueError):
        ResourcePolicy(wall_s=1, cpu_s=2, memory_mb=256, max_output_bytes=1, max_processes=1, max_calls=1)
    with pytest.raises(ValueError):
        ResourcePolicy(wall_s=1, cpu_s=1, memory_mb=0, max_output_bytes=1, max_processes=1, max_calls=1)


def test_task_kinds_are_explicit() -> None:
    he = Task("HumanEval/0", prompt="def f(x):\n", tests=CheckFunction("def check(candidate): pass", "f"))
    mb = Task("MBPP/2", prompt="", tests=AssertList(("assert f(1) == 1",), setup="import math"))
    ut = Task("U/1", prompt="", tests=UnittestModule("import unittest\nclass T(unittest.TestCase):\n  pass"))
    assert {type(t.tests) for t in (he, mb, ut)} == {CheckFunction, AssertList, UnittestModule}


def test_expected_unittest_ids_are_read_from_the_test_source_not_from_output() -> None:
    src = """
import unittest
class TestA(unittest.TestCase):
    def setUp(self): pass
    def test_one(self): pass
    def test_two(self): pass
    def helper(self): pass
class NotATest:
    def test_x(self): pass
class TestB(unittest.TestCase):
    def test_three(self): pass
"""
    assert expected_unittest_ids(src) == ("TestA.test_one", "TestA.test_two", "TestB.test_three")
