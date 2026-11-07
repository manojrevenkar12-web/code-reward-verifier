"""Lanes: RL rollouts arrive in bursts of thousands; eval jobs are small and someone is waiting. A weighted
fair scheduler gives each lane its share whenever it has work, and lends idle capacity to the others."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crv.service.lanes import LaneFull, LaneScheduler


def test_weights_set_the_share_while_both_lanes_have_work() -> None:
    s = LaneScheduler({"rl": 3, "eval": 1}, capacity={"rl": 10_000, "eval": 10_000})
    for i in range(4000):
        s.push("rl", i)
    for i in range(4000):
        s.push("eval", i)
    first = [s.pop()[0] for _ in range(400)]
    assert first.count("rl") == 300 and first.count("eval") == 100


def test_an_idle_lane_lends_its_share() -> None:
    s = LaneScheduler({"rl": 3, "eval": 1}, capacity={"rl": 100, "eval": 100})
    for i in range(10):
        s.push("eval", i)
    assert [s.pop()[0] for _ in range(10)] == ["eval"] * 10
    assert s.pop() is None


def test_eval_work_is_not_stuck_behind_an_rl_burst() -> None:
    s = LaneScheduler({"rl": 3, "eval": 1}, capacity={"rl": 100_000, "eval": 100})
    for i in range(50_000):
        s.push("rl", i)
    s.push("eval", "urgent")
    position = next(n for n in range(10) if s.pop() == ("eval", "urgent"))
    assert position <= 4  # within one round, not after 50,000 rollouts


def test_full_lanes_push_back() -> None:
    s = LaneScheduler({"rl": 1}, capacity={"rl": 2})
    s.push("rl", 1)
    s.push("rl", 2)
    with pytest.raises(LaneFull):
        s.push("rl", 3)
    assert s.depth() == {"rl": 2}


def test_unknown_lanes_are_rejected() -> None:
    s = LaneScheduler({"rl": 1}, capacity={"rl": 2})
    with pytest.raises(KeyError):
        s.push("training", 1)


@given(st.lists(st.tuples(st.sampled_from(["a", "b", "c"]), st.integers()), max_size=200))
def test_every_item_comes_out_exactly_once_in_lane_order(pushes: list[tuple[str, int]]) -> None:
    s = LaneScheduler({"a": 1, "b": 2, "c": 5}, capacity={"a": 1000, "b": 1000, "c": 1000})
    for lane, x in pushes:
        s.push(lane, x)
    out = []
    while (item := s.pop()) is not None:
        out.append(item)
    assert sorted(out, key=repr) == sorted(pushes, key=repr)
    for lane in "abc":
        assert [x for ln, x in out if ln == lane] == [x for ln, x in pushes if ln == lane]
