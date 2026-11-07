"""The sandbox contract. Each test runs real untrusted code in a forked, hardened child."""

import os
import time

import pytest

from crv.domain.policy import ResourcePolicy
from crv.sandbox.zygote import ChildCrashed, ChildRaised, ChildTimeout, ZygoteClient

pytestmark = pytest.mark.sandbox

POLICY = ResourcePolicy(
    wall_s=5, cpu_s=3, memory_mb=256, max_output_bytes=100_000, max_processes=8, max_calls=100
)


def run(z: ZygoteClient, source: str, name: str = "f", *args: object, policy: ResourcePolicy = POLICY) -> object:
    with z.spawn(policy) as child:
        child.load(source)
        return child.call(name, list(args), {})


def test_calls_return_plain_values(zygote: ZygoteClient) -> None:
    assert run(zygote, "def f(a, b):\n    return [a + b, (a, b)]", "f", 2, 3) == [5, (2, 3)]


def test_the_child_is_not_root_and_has_no_network(zygote: ZygoteClient) -> None:
    src = """
import os, socket
def f():
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=1)
        net = "open"
    except OSError as e:
        net = type(e).__name__
    return [os.getuid(), net]
"""
    uid, net = run(zygote, src)  # type: ignore[misc]
    assert uid != 0
    assert net != "open"


def test_the_child_cannot_write_outside_its_private_tmp(zygote: ZygoteClient) -> None:
    src = """
def f(path):
    try:
        open(path, "w").write("x")
        return "wrote"
    except OSError as e:
        return type(e).__name__
"""
    assert run(zygote, src, "f", "/etc/crv-probe") == "PermissionError"
    assert run(zygote, src, "f", "/tmp/crv-probe") == "wrote"


def test_tmp_is_private_to_each_task(zygote: ZygoteClient) -> None:
    write = "def f():\n    open('/tmp/shared-answer', 'w').write('42')\n    return 1"
    read = "import os\ndef f():\n    return os.path.exists('/tmp/shared-answer')"
    assert run(zygote, write) == 1
    assert run(zygote, read) is False  # nothing carries over from one rollout to the next


def test_the_environment_is_scrubbed(zygote: ZygoteClient) -> None:
    os.environ["CRV_TEST_SECRET"] = "hunter2"
    try:
        assert run(zygote, "import os\ndef f():\n    return sorted(os.environ)") == ["HOME", "LANG", "PATH"]
    finally:
        del os.environ["CRV_TEST_SECRET"]


def test_infinite_loops_time_out_and_are_killed(zygote: ZygoteClient) -> None:
    fast = ResourcePolicy(wall_s=1, cpu_s=1, memory_mb=128, max_output_bytes=1000, max_processes=4, max_calls=10)
    t0 = time.monotonic()
    with pytest.raises(ChildTimeout):
        run(zygote, "def f():\n    while True:\n        pass", policy=fast)
    assert time.monotonic() - t0 < 3


def test_memory_is_capped(zygote: ZygoteClient) -> None:
    with pytest.raises(ChildRaised) as e:
        run(zygote, "def f():\n    return len(bytearray(1024 * 1024 * 1024))")
    assert e.value.type_name == "MemoryError"


def test_fork_bombs_hit_the_process_limit_and_leave_nothing_behind(zygote: ZygoteClient) -> None:
    src = """
import os, time
def f():
    pids = 0
    try:
        while True:
            if os.fork() == 0:
                time.sleep(60)
                os._exit(0)
            pids += 1
    except OSError:
        return pids
"""
    forked = run(zygote, src)
    assert isinstance(forked, int) and forked < POLICY.max_processes
    assert zygote.processes_left() == 0


def test_daemonised_grandchildren_are_reaped(zygote: ZygoteClient) -> None:
    src = """
import os, time
def f():
    if os.fork() == 0:
        os.setsid()
        time.sleep(120)
        os._exit(0)
    return 1
"""
    assert run(zygote, src) == 1
    assert zygote.processes_left() == 0


def test_output_floods_are_cut_off(zygote: ZygoteClient) -> None:
    with pytest.raises(ChildCrashed, match="output"):
        run(zygote, "import sys\ndef f():\n    while True:\n        sys.stdout.write('x' * 65536)")


def test_exiting_early_is_a_crash_not_a_result(zygote: ZygoteClient) -> None:
    with pytest.raises(ChildCrashed, match="exited"):
        run(zygote, "print(True)\nimport os\nos._exit(0)\ndef f():\n    return 1")


def test_exceptions_come_back_by_type(zygote: ZygoteClient) -> None:
    with pytest.raises(ChildRaised) as e:
        run(zygote, "def f():\n    raise ValueError('nope')")
    assert (e.value.type_name, e.value.message) == ("ValueError", "nope")


def test_non_data_results_are_refused_inside_the_child(zygote: ZygoteClient) -> None:
    src = "class Eq:\n    def __eq__(self, o):\n        return True\ndef f():\n    return Eq()"
    with pytest.raises(ChildRaised) as e:
        run(zygote, src)
    assert e.value.type_name == "NonDataResult"


def test_the_zygote_holds_no_task_data(zygote: ZygoteClient) -> None:
    # A child can read everything its zygote had in memory at fork time. Sources and tests therefore travel
    # to the child over its own channel after the fork; a later child must not find an earlier one's source.
    marker = "def f():\n    return 'needle-7f3a9c'"
    assert run(zygote, marker) == "needle-7f3a9c"
    scan = """
import gc
def f():
    hits = 0
    for o in gc.get_objects():
        if isinstance(o, str) and "needle-7f3a" + "9c" in o and "hits" not in o:
            hits += 1
    return hits
"""
    assert run(zygote, scan) == 0


def test_children_run_in_parallel_from_separate_zygotes() -> None:
    a, b = ZygoteClient.start(slot=8), ZygoteClient.start(slot=9)
    try:
        sleep = "import time\ndef f():\n    time.sleep(0.5)\n    return 1"
        t0 = time.monotonic()
        with a.spawn(POLICY) as ca, b.spawn(POLICY) as cb:
            ca.load(sleep)
            cb.load(sleep)
            ca.send_call("f", [], {})
            cb.send_call("f", [], {})
            assert ca.receive_result() == 1 and cb.receive_result() == 1
        assert time.monotonic() - t0 < 0.9
    finally:
        a.close()
        b.close()
