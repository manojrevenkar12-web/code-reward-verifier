"""The zygote: a clean, pre-warmed process that forks one hardened child per task.

Why a separate process: a forked child can read everything its parent had in memory. The trusted side holds
tests and expected values, so it must never be the process that forks untrusted code. The zygote only ever
holds pre-imported standard modules; sources and tests reach the child over the child's own channel after
the fork, so nothing from one task is left in the zygote for the next.

    worker (trusted, holds tests) ──control──▶ zygote (clean) ──fork──▶ child (hardened, runs completion)
             ◀────────────────────── channel + output pipe (passed with SCM_RIGHTS) ──────────────────▶
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict
from types import TracebackType
from typing import Any

from crv import config
from crv.domain.policy import ResourcePolicy
from crv.domain.values import decode, encode
from crv.sandbox import frames, hardening

_CONTROL_MAX = 64 * 1024


class ChildError(Exception):
    """Base for what can go wrong on the untrusted side."""


class ChildTimeout(ChildError):
    pass


class ChildCrashed(ChildError):
    """The child stopped before answering: it exited, was killed, flooded its output or broke the protocol."""

    def __init__(self, reason: str, kind: str = "crashed") -> None:
        super().__init__(reason)
        self.kind = kind  # "crashed" | "resource" | "protocol"


class ChildRaised(ChildError):
    def __init__(self, type_name: str, message: str) -> None:
        super().__init__(f"{type_name}: {message}")
        self.type_name = type_name
        self.message = message


class ChildLoadError(ChildRaised):
    """The completion failed at import time (syntax error, top-level exception, sys.exit)."""


# ---------------------------------------------------------------------------------------------------------
# Zygote process


def _zygote_main(control_fd: int, slot: int) -> None:
    settings = config.load("sandbox.json")
    for name in settings["prewarm"]:
        importlib.import_module(name)
    import crv.sandbox.child  # noqa: F401  (imported before any fork)

    control = socket.socket(fileno=control_fd)
    uid = settings["uid_base"] + slot
    hardening.die_with_parent()  # a replaced worker takes its zygote (and through it, its children) along
    hardening.become_subreaper()
    _sweep(uid)  # whatever a previous zygote for this slot left behind
    network = hardening.isolate_network()
    isolation = {"network": network, "uid_drop": hardening.is_root(), "private_tmp": hardening.is_root()}
    control.send(json.dumps({"ready": True, "isolation": isolation, "uid": uid}).encode())
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    while True:
        raw = control.recv(_CONTROL_MAX)
        if not raw:
            return
        msg = json.loads(raw)
        op = msg["op"]
        if op == "spawn":
            _spawn(control, ResourcePolicy(**msg["policy"]), uid, settings)
        elif op == "finish":
            control.send(json.dumps(_finish(msg["pid"], msg.get("grace_ms", 0), uid)).encode())
        elif op == "count":
            control.send(json.dumps({"count": hardening.count_processes(uid)}).encode())
        elif op == "exit":
            return


def _spawn(control: socket.socket, policy: ResourcePolicy, uid: int, settings: dict[str, Any]) -> None:
    parent_end, child_end = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    out_r, out_w = os.pipe()
    pid = os.fork()
    if pid == 0:  # child
        try:
            control.close()
            parent_end.close()
            os.close(out_r)
            os.setsid()
            hardening.die_with_parent()
            hardening.private_tmp(settings["tmpfs_mb"])
            hardening.set_limits(policy, settings["file_mb"], settings["max_open_files"])
            hardening.drop_privileges(uid)
            hardening.no_new_privileges()
            hardening.scrub_environment()
            os.chdir("/tmp")
            devnull = os.open(os.devnull, os.O_RDONLY)
            os.dup2(devnull, 0)
            os.dup2(out_w, 1)
            os.dup2(out_w, 2)
            keep = {0, 1, 2, child_end.fileno()}
            for fd in range(3, 1024):
                if fd not in keep:
                    with contextlib.suppress(OSError):
                        os.close(fd)
            from crv.sandbox import child

            child.serve(child_end)
        finally:
            os._exit(71)
    child_end.close()
    os.close(out_w)
    socket.send_fds(control, [json.dumps({"pid": pid}).encode()], [parent_end.fileno(), out_r])
    parent_end.close()
    os.close(out_r)


def _finish(pid: int, grace_ms: int, uid: int) -> dict[str, Any]:
    status = None
    end = time.monotonic() + grace_ms / 1000
    while True:
        try:
            done, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            done = pid
            break
        if done or time.monotonic() >= end:
            break
        time.sleep(0.001)
    killed = False
    if not done:
        killed = True
        try:
            os.kill(pid, signal.SIGKILL)
            _, status = os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
    _sweep(uid)
    sig = os.WTERMSIG(status) if status is not None and os.WIFSIGNALED(status) else None
    code = os.WEXITSTATUS(status) if status is not None and os.WIFEXITED(status) else None
    return {"killed": killed, "signal": sig, "code": code}


def _sweep(uid: int) -> None:
    """Kill every process left running as this slot's uid (daemons, fork bombs). kill(-1) from a process
    with that uid reaches exactly those processes."""
    if not hardening.is_root():
        return
    for _ in range(3):
        hardening.reap_all()
        if hardening.count_processes(uid) == 0:
            return
        reaper = os.fork()
        if reaper == 0:
            try:
                os.setgid(uid)
                os.setuid(uid)
                os.kill(-1, signal.SIGKILL)
            finally:
                os._exit(0)
        os.waitpid(reaper, 0)
        time.sleep(0.002)
    hardening.reap_all()


# ---------------------------------------------------------------------------------------------------------
# Trusted-side handles


class ZygoteClient:
    def __init__(self, proc: subprocess.Popen[bytes], control: socket.socket, hello: dict[str, Any]) -> None:
        self.proc = proc
        self.control = control
        self.isolation = hardening.Isolation(**hello["isolation"])
        self.uid: int = hello["uid"]

    @classmethod
    def start(cls, slot: int) -> ZygoteClient:
        ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
        if "CRV_CONFIG_DIR" in os.environ:
            env["CRV_CONFIG_DIR"] = os.environ["CRV_CONFIG_DIR"]
        env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
        fd = theirs.fileno()
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                f"from crv.sandbox.zygote import _zygote_main; _zygote_main({fd}, {slot})",
            ],
            pass_fds=(fd,),
            env=env,
            stdin=subprocess.DEVNULL,
        )
        theirs.close()
        hello = json.loads(ours.recv(_CONTROL_MAX))
        return cls(proc, ours, hello)

    def _ask(self, msg: dict[str, Any]) -> dict[str, Any]:
        self.control.send(json.dumps(msg).encode())
        reply: dict[str, Any] = json.loads(self.control.recv(_CONTROL_MAX))
        return reply

    def spawn(self, policy: ResourcePolicy) -> Child:
        self.control.send(json.dumps({"op": "spawn", "policy": asdict(policy)}).encode())
        raw, fds, _, _ = socket.recv_fds(self.control, _CONTROL_MAX, 2)
        if len(fds) != 2:
            raise RuntimeError("zygote did not pass the child's channel")
        pid = json.loads(raw)["pid"]
        return Child(self, pid, socket.socket(fileno=fds[0]), fds[1], policy)

    def finish(self, pid: int, grace_ms: int = 0) -> dict[str, Any]:
        return self._ask({"op": "finish", "pid": pid, "grace_ms": grace_ms})

    def processes_left(self) -> int:
        return int(self._ask({"op": "count"})["count"])

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.control.send(json.dumps({"op": "exit"}).encode())
        self.control.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class Child:
    """One task's sandboxed process, seen from the trusted side."""

    def __init__(self, zygote: ZygoteClient, pid: int, chan: socket.socket, out_fd: int, policy: ResourcePolicy):
        self.zygote = zygote
        self.pid = pid
        self.chan = chan
        self.out_fd = out_fd
        self.policy = policy
        self.started = time.monotonic()
        self.deadline = self.started + policy.wall_s
        self.output = bytearray()
        self.output_total = 0
        self.calls = 0
        self.exit: dict[str, Any] | None = None
        max_frame = int(config.load("sandbox.json")["max_frame_bytes"])
        self.reader = frames.Reader(chan, out_fd, self._on_output, max_frame)

    def __enter__(self) -> Child:
        return self

    def __exit__(self, et: type[BaseException] | None, e: BaseException | None, tb: TracebackType | None) -> None:
        self.close()

    def _on_output(self, chunk: bytes) -> None:
        self.output_total += len(chunk)
        self.output += chunk
        if len(self.output) > 4096:
            del self.output[:-4096]  # keep the tail for the trace; output is never parsed for results
        if self.output_total > self.policy.max_output_bytes:
            self._stop()
            raise ChildCrashed(f"output limit: more than {self.policy.max_output_bytes} bytes", kind="resource")

    def _stop(self, grace_ms: int = 0) -> dict[str, Any]:
        if self.exit is None:
            self.exit = self.zygote.finish(self.pid, grace_ms)
        return self.exit

    def _recv(self) -> Any:
        try:
            return self.reader.recv(self.deadline)
        except frames.Deadline:
            self._stop()
            raise ChildTimeout(f"wall limit: {self.policy.wall_s} s") from None
        except frames.PeerClosed:
            status = self._stop(grace_ms=200)
            if status.get("signal") in (signal.SIGXCPU, signal.SIGKILL) and not status.get("killed"):
                raise ChildTimeout(f"CPU limit: {self.policy.cpu_s} s") from None
            how = f"signal {status['signal']}" if status.get("signal") else f"code {status.get('code')}"
            raise ChildCrashed(f"exited ({how}) before the tests finished") from None
        except frames.FrameError as e:
            self._stop()
            raise ChildCrashed(f"protocol violation: {e}", kind="protocol") from None

    def _send(self, message: Any) -> None:
        try:
            frames.send(self.chan, message)
        except OSError:
            self._recv()  # surfaces why the child is gone
            raise ChildCrashed("channel closed", kind="protocol") from None

    def load(self, source: str) -> dict[str, Any]:
        self._send(["load", source])
        msg = self._recv()
        if isinstance(msg, list) and len(msg) == 3 and msg[0] == "load_error":
            raise ChildLoadError(str(msg[1]), str(msg[2]))
        if not (isinstance(msg, list) and len(msg) == 2 and msg[0] == "ready" and isinstance(msg[1], dict)):
            self._stop()
            raise ChildCrashed("protocol violation: expected ready", kind="protocol")
        names: dict[str, Any] = msg[1]
        return names

    def send_call(self, name: str, args: list[Any], kwargs: dict[str, Any]) -> None:
        self.calls += 1
        if self.calls > self.policy.max_calls:
            self._stop()
            raise ChildCrashed(f"call limit: more than {self.policy.max_calls} calls", kind="resource")
        self._send(["call", name, encode(args), encode(kwargs)])

    def receive_result(self) -> object:
        msg = self._recv()
        if isinstance(msg, list) and len(msg) == 2 and msg[0] == "ok" and isinstance(msg[1], str):
            try:
                return decode(msg[1])
            except Exception as e:
                self._stop()
                raise ChildCrashed(f"protocol violation: {e}", kind="protocol") from None
        if isinstance(msg, list) and len(msg) == 3 and msg[0] == "raise":
            raise ChildRaised(str(msg[1])[:100], str(msg[2])[:500])
        self._stop()
        raise ChildCrashed("protocol violation: expected a result", kind="protocol")

    def call(self, name: str, args: list[Any], kwargs: dict[str, Any]) -> object:
        self.send_call(name, args, kwargs)
        return self.receive_result()

    def send_tests(self, source: str) -> None:
        self._send(["tests", source])

    def receive(self) -> Any:
        return self._recv()

    def close(self) -> None:
        if self.exit is None:
            with contextlib.suppress(OSError):
                frames.send(self.chan, ["done"])
            self._stop(grace_ms=50)
        self.chan.close()
        with contextlib.suppress(OSError):
            os.close(self.out_fd)

    @property
    def wall_ms(self) -> float:
        return (time.monotonic() - self.started) * 1000
