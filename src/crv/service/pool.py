"""A pool of trusted worker processes. Each owns one zygote and verifies one completion at a time.

The event loop never runs tests or untrusted code: it schedules work across lanes, ships submissions to idle
workers over pipes, and replaces any worker that overruns its deadline (the verdict is then an infra error,
which carries no reward)."""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import time
from dataclasses import asdict, dataclass, field
from multiprocessing.connection import Connection
from typing import Any

from crv.domain.policy import ResourcePolicy
from crv.domain.task import Submission
from crv.domain.verdict import Outcome, Trust, Verdict
from crv.service.lanes import LaneScheduler
from crv.service.schema import submission_from, submission_to


def _worker_main(conn: Connection, slot: int) -> None:
    from crv.sandbox.zygote import ZygoteClient
    from crv.verify.verifier import Verifier

    zygote = ZygoteClient.start(slot)
    verifier = Verifier(zygote)
    conn.send({"isolation": asdict(zygote.isolation)})
    while True:
        try:
            msg = conn.recv()
        except EOFError:
            break
        if msg is None:
            break
        sub = submission_from(msg["submission"])
        try:
            v = verifier.verify(sub, ResourcePolicy(**msg["policy"]))
        except Exception as e:  # the verifier itself failed: no reward either way
            v = Verdict(Outcome.INFRA_ERROR, f"verifier error: {type(e).__name__}: {e}"[:300], Trust.SPLIT)
        conn.send(v.to_dict())
    zygote.close()


@dataclass
class _Worker:
    slot: int
    proc: mp.process.BaseProcess
    conn: Connection
    isolation: dict[str, bool]
    job: _Job | None = None
    started: float = 0.0


@dataclass
class _Job:
    submission: Submission
    lane: str
    policy: ResourcePolicy
    future: asyncio.Future[dict[str, Any]]
    queued: float = field(default_factory=time.monotonic)


class WorkerPool:
    def __init__(
        self,
        workers: int,
        slot_base: int,
        policies: dict[str, ResourcePolicy],
        scheduler: LaneScheduler,
        watchdog_grace_s: float,
    ) -> None:
        self.size = workers
        self.slot_base = slot_base
        self.policies = policies
        self.scheduler = scheduler
        self.grace = watchdog_grace_s
        self.workers: list[_Worker] = []
        self.ctx = mp.get_context("spawn")
        self.loop: asyncio.AbstractEventLoop | None = None
        self.completed = 0
        self.replaced = 0
        self._watchdog: asyncio.Task[None] | None = None
        self._relaunches: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        for i in range(self.size):
            self.workers.append(await self._launch(self.slot_base + i))
        self._watchdog = asyncio.create_task(self._watch())

    async def _launch(self, slot: int) -> _Worker:
        ours, theirs = self.ctx.Pipe()
        proc = self.ctx.Process(target=_worker_main, args=(theirs, slot), daemon=True)
        proc.start()
        theirs.close()
        hello = await asyncio.to_thread(ours.recv)
        w = _Worker(slot, proc, ours, hello["isolation"])
        assert self.loop is not None
        self.loop.add_reader(ours.fileno(), self._on_reply, w)
        return w

    @property
    def isolation(self) -> dict[str, bool]:
        keys = ("network", "uid_drop", "private_tmp")
        return {k: all(w.isolation.get(k, False) for w in self.workers) for k in keys}

    def verify(self, sub: Submission, lane: str) -> asyncio.Future[dict[str, Any]]:
        assert self.loop is not None
        job = _Job(sub, lane, self.policies[lane], self.loop.create_future())
        self.scheduler.push(lane, job)  # raises LaneFull
        self._dispatch()
        return job.future

    def _dispatch(self) -> None:
        for w in self.workers:
            if w.job is not None:
                continue
            nxt = self.scheduler.pop()
            if nxt is None:
                return
            _, job = nxt
            w.job, w.started = job, time.monotonic()
            w.conn.send({"submission": submission_to(job.submission), "policy": asdict(job.policy)})

    def _on_reply(self, w: _Worker) -> None:
        try:
            verdict = w.conn.recv()
        except (EOFError, OSError):
            self._replace(w, "worker exited")
            return
        job, w.job = w.job, None
        self.completed += 1
        if job is not None and not job.future.done():
            verdict["queue_ms"] = round((w.started - job.queued) * 1000, 1)
            job.future.set_result(verdict)
        self._dispatch()

    def _replace(self, w: _Worker, why: str) -> None:
        assert self.loop is not None
        self.loop.remove_reader(w.conn.fileno())
        w.proc.kill()
        w.conn.close()
        job, w.job = w.job, None
        if job is not None and not job.future.done():
            v = Verdict(Outcome.INFRA_ERROR, f"worker replaced: {why}", Trust.SPLIT)
            job.future.set_result({**v.to_dict(), "queue_ms": 0.0})
        self.replaced += 1
        idx = self.workers.index(w)

        async def relaunch() -> None:
            self.workers[idx] = await self._launch(w.slot)
            self._dispatch()

        self.workers[idx] = _Worker(w.slot, w.proc, w.conn, w.isolation, job=_PLACEHOLDER)
        task = asyncio.ensure_future(relaunch())
        self._relaunches.add(task)
        task.add_done_callback(self._relaunches.discard)

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            now = time.monotonic()
            for w in list(self.workers):
                if (
                    w.job is not None
                    and w.job is not _PLACEHOLDER
                    and now - w.started > w.job.policy.wall_s + self.grace
                ):
                    self._replace(w, "deadline overrun")

    async def close(self) -> None:
        if self._watchdog:
            self._watchdog.cancel()
        for w in self.workers:
            try:
                assert self.loop is not None
                self.loop.remove_reader(w.conn.fileno())
                w.conn.send(None)
            except (OSError, ValueError):
                pass
        for w in self.workers:
            await asyncio.to_thread(w.proc.join, 5)
            if w.proc.is_alive():
                w.proc.kill()


_PLACEHOLDER: Any = object()  # marks a slot whose worker is being relaunched
