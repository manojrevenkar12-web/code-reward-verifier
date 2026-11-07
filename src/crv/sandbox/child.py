"""The sandboxed side. Everything here runs after hardening, in the same process as the untrusted completion,
so nothing here is trusted by the parent: the parent validates every frame it receives.

Protocol (child view): receive ["load", source] → reply ["ready", names] or ["load_error", type, message];
then serve ["call", name, args, kwargs] → ["ok", value] | ["raise", type, message] until ["done"].
In unittest mode the parent sends ["tests", source] after "ready" and the child streams one record per test.
"""

import builtins
import os
import socket
import types
import unittest
from collections.abc import Callable
from typing import Any, NoReturn

from crv.domain.values import EncodeError, Limits, decode, encode
from crv.sandbox import frames

_NAME_LIMITS = Limits(max_nodes=10_000, max_bytes=256 * 1024)


def _message(e: BaseException) -> str:
    try:
        return str(e)[:500]
    except Exception:
        return "<unprintable>"


def serve(chan: socket.socket) -> NoReturn:
    try:
        _serve(chan)
    finally:
        os._exit(0)


def _serve(chan: socket.socket) -> None:
    load = frames.recv_blocking(chan)
    if not (isinstance(load, list) and len(load) == 2 and load[0] == "load" and isinstance(load[1], str)):
        return
    ns: dict[str, Any] = {"__name__": "__main__", "__builtins__": builtins}
    pristine = {k: get() for k, get in _GUARDED.items()}  # before any completion code runs
    try:
        exec(compile(load[1], "<completion>", "exec"), ns)
    except BaseException as e:  # includes SystemExit: a completion that exits at import time has failed
        frames.send(chan, ["load_error", type(e).__name__, _message(e)])
        return
    frames.send(chan, ["ready", _describe(ns)])
    while True:
        msg = frames.recv_blocking(chan)
        if not isinstance(msg, list) or not msg:
            return
        if msg[0] == "call" and len(msg) == 4:
            frames.send(chan, _call(ns, msg[1], msg[2], msg[3]))
        elif msg[0] == "tests" and len(msg) == 2:
            _run_unittests(chan, ns, msg[1], pristine)
        else:
            return


def _describe(ns: dict[str, Any]) -> dict[str, Any]:
    """Module-level names the tests may use: callables become proxies on the trusted side; small data values
    are copied; anything else is opaque."""
    out: dict[str, Any] = {}
    for name, value in ns.items():
        if name.startswith("__"):
            continue
        if isinstance(value, types.ModuleType):
            # The tests may use a module the completion imported (the reference harness shares one namespace).
            out[name] = ["module", value.__name__]
            continue
        if callable(value):
            out[name] = ["callable"]
            continue
        try:
            out[name] = ["data", encode(value, _NAME_LIMITS)]
        except EncodeError:
            out[name] = ["opaque"]
    return out


def _call(ns: dict[str, Any], name: object, args: object, kwargs: object) -> list[Any]:
    if not isinstance(name, str) or not isinstance(args, str) or not isinstance(kwargs, str):
        return ["raise", "ProtocolError", "bad call frame"]
    fn = ns.get(name)
    if fn is None:
        return ["raise", "NameError", f"name {name!r} is not defined"]
    try:
        a = decode(args)
        k = decode(kwargs)
        assert isinstance(a, list) and isinstance(k, dict)
        result = fn(*a, **k)
    except BaseException as e:
        return ["raise", type(e).__name__, _message(e)]
    try:
        return ["ok", encode(result)]
    except EncodeError as e:
        return ["raise", "NonDataResult", _message(e)]
    except BaseException as e:  # a generator that raises while being materialised
        return ["raise", type(e).__name__, _message(e)]


class _ChannelResult(unittest.TestResult):
    """Reports each test the moment it finishes. Nothing is parsed from printed output."""

    def __init__(self, chan: socket.socket) -> None:
        super().__init__()
        self.chan = chan

    def _report(self, test: unittest.TestCase, status: str, detail: str = "") -> None:
        cls = type(test).__name__
        frames.send(self.chan, ["test", f"{cls}.{test._testMethodName}", status, detail[:500]])

    def addSuccess(self, test: unittest.TestCase) -> None:
        self._report(test, "pass")

    def addFailure(self, test: unittest.TestCase, err: Any) -> None:
        self._report(test, "fail", _message(err[1]))

    def addError(self, test: unittest.TestCase, err: Any) -> None:
        self._report(test, "error", f"{err[0].__name__}: {_message(err[1])}")

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        self._report(test, "skip", reason)

    def addExpectedFailure(self, test: unittest.TestCase, err: Any) -> None:
        self._report(test, "pass")

    def addUnexpectedSuccess(self, test: unittest.TestCase) -> None:
        self._report(test, "fail", "unexpected success")


_GUARDED: dict[str, Callable[[], object]] = {
    "TestCase.run": lambda: unittest.TestCase.run,
    "TestCase.assertEqual": lambda: unittest.TestCase.assertEqual,
    "TestCase.assertTrue": lambda: unittest.TestCase.assertTrue,
    "TestCase.fail": lambda: unittest.TestCase.fail,
    "TestResult.addFailure": lambda: unittest.TestResult.addFailure,
    "builtins.isinstance": lambda: builtins.isinstance,
    "builtins.AssertionError": lambda: builtins.AssertionError,
    "os._exit": lambda: os._exit,
}


def _run_unittests(chan: socket.socket, ns: dict[str, Any], source: object, before: dict[str, object]) -> None:
    """Unittest mode. The completion was already loaded into `ns`; the test module runs in the same namespace
    (as the reference harness does). Tampering with unittest or builtins is detected by identity checks."""
    if not isinstance(source, str):
        return
    try:
        exec(compile(source, "<tests>", "exec"), ns)
        suite = unittest.defaultTestLoader.loadTestsFromModule(types.SimpleNamespace(**ns))  # type: ignore[arg-type]
        suite.run(_ChannelResult(chan))
    except BaseException as e:
        frames.send(chan, ["suite_error", type(e).__name__, _message(e)])
    changed = sorted(k for k, get in _GUARDED.items() if get() is not before[k])
    frames.send(chan, ["tests_done", changed])
