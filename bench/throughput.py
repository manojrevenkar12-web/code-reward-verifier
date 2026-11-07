"""Throughput and latency on the same honest completions (reference solutions of HumanEval + MBPP).

    sudo python bench/throughput.py            # writes reports/throughput.json

Rows:
- eval-framework, Docker: their real path (llm-sandbox container pool, one sample at a time as their metric
  calls it). Container pool start-up is reported separately and excluded from the rate.
- eval-framework, fresh process: their programs and scoring, one new Python process per sample.
- this verifier, direct: one worker, the Verifier called in a loop.
- this verifier, service: the HTTP API with N workers, batches of 64.
Then the lanes experiment: an RL burst on the rl lane while single eval requests arrive; eval latency with
lanes (weights rl 3 : eval 1) against one shared FIFO lane.
"""

from __future__ import annotations

import asyncio
import json
import random
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from crv import config  # noqa: E402
from crv.datasets import Example, humaneval, mbpp  # noqa: E402
from crv.domain.policy import load_policies  # noqa: E402
from crv.domain.task import Submission  # noqa: E402
from crv.service.schema import submission_to  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def pct(xs: list[float]) -> dict[str, float]:
    s = sorted(xs)

    def q(p: float) -> float:
        return round(s[min(len(s) - 1, int(p * len(s)))], 1)

    return {"p50": q(0.5), "p95": q(0.95), "p99": q(0.99)}


def run_reference(examples: list[Example], runtime: str) -> dict[str, Any]:
    import reference

    warm = time.perf_counter()
    reference.score(reference.program(examples[0], examples[0].reference), runtime)
    startup_s = time.perf_counter() - warm
    lat: list[float] = []
    passed = 0
    t0 = time.perf_counter()
    for ex in examples:
        a = time.perf_counter()
        passed += reference.score(reference.program(ex, ex.reference), runtime) is True
        lat.append((time.perf_counter() - a) * 1000)
    wall = time.perf_counter() - t0
    return {"completions": len(examples), "passed": passed, "per_s": round(len(examples) / wall, 1),
            "latency_ms": pct(lat), "startup_s": round(startup_s, 1)}


def run_direct(examples: list[Example]) -> dict[str, Any]:
    from crv.sandbox.zygote import ZygoteClient
    from crv.verify.verifier import Verifier

    z = ZygoteClient.start(slot=60)
    v = Verifier(z)
    policy = load_policies()["eval"]
    lat: list[float] = []
    passed = 0
    t0 = time.perf_counter()
    for ex in examples:
        a = time.perf_counter()
        passed += v.verify(Submission(ex.task, ex.reference), policy).outcome.value == "passed"
        lat.append((time.perf_counter() - a) * 1000)
    wall = time.perf_counter() - t0
    z.close()
    return {"completions": len(examples), "passed": passed, "per_s": round(len(examples) / wall, 1),
            "latency_ms": pct(lat)}


class Server:
    """The real service (uvicorn) in a background thread."""

    def __init__(self, settings: dict[str, Any], port: int) -> None:
        import uvicorn

        from crv.service.http import create_app

        self.server = uvicorn.Server(uvicorn.Config(create_app(settings), port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.url = f"http://127.0.0.1:{port}"

    def __enter__(self) -> Server:
        self.thread.start()
        while not self.server.started:
            time.sleep(0.05)
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(10)


def run_service(examples: list[Example], workers: int) -> dict[str, Any]:
    import httpx

    settings = {**config.load("service.json"), "workers": workers, "slot_base": 70}
    items = [submission_to(Submission(ex.task, ex.reference)) for ex in examples]
    batches = [items[i : i + 64] for i in range(0, len(items), 64)]
    with Server(settings, 8711) as srv, httpx.Client(base_url=srv.url, timeout=600) as c:
        c.post("/v1/verify", json={"lane": "eval", "items": items[:4]})  # warm
        t0 = time.perf_counter()
        passed = 0

        def send(batch: list[dict[str, Any]]) -> int:
            r = c.post("/v1/verify", json={"lane": "eval", "items": batch})
            return sum(v["outcome"] == "passed" for v in r.json()["verdicts"])

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(4) as pool:
            passed = sum(pool.map(send, batches))
        wall = time.perf_counter() - t0
    return {"completions": len(items), "passed": passed, "per_s": round(len(items) / wall, 1), "workers": workers}


def run_lanes(examples: list[Example], workers: int, separate: bool) -> dict[str, Any]:
    """RL burst plus a trickle of single eval requests. separate=False sends both to one FIFO lane."""
    import httpx

    lanes = {"rl": {"weight": 3, "capacity": 100_000}, "eval": {"weight": 1, "capacity": 10_000}}
    settings = {**config.load("service.json"), "workers": workers, "slot_base": 80, "lanes": lanes}
    rng = random.Random(3)
    burst = [submission_to(Submission(ex.task, ex.reference)) for ex in rng.choices(examples, k=2000)]
    probes = [submission_to(Submission(ex.task, ex.reference)) for ex in rng.choices(examples, k=40)]
    eval_lane = "eval" if separate else "rl"

    async def main(url: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=url, timeout=900) as c:
            await c.post("/v1/verify", json={"lane": "eval", "items": probes[:2]})
            rl_task = asyncio.create_task(c.post("/v1/verify", json={"lane": "rl", "items": burst}))
            await asyncio.sleep(0.3)
            lat: list[float] = []
            for p in probes:
                a = time.perf_counter()
                await c.post("/v1/verify", json={"lane": eval_lane, "items": [p]})
                lat.append((time.perf_counter() - a) * 1000)
                await asyncio.sleep(0.05)
            t0 = time.perf_counter()
            await rl_task
            return {"eval_latency_ms": pct(lat), "rl_burst": len(burst), "rl_finished_after_probes_s": round(time.perf_counter() - t0, 1)}

    with Server(settings, 8712 if separate else 8713) as srv:
        return asyncio.run(main(srv.url))


if __name__ == "__main__":
    exs = humaneval() + mbpp()
    sample = random.Random(11).sample(exs, 300)
    out: dict[str, Any] = {"host": {"cpus": __import__("os").cpu_count()}}
    out["eval_framework_docker"] = {**run_reference(sample, "docker"), "sample": 300}
    print("docker", out["eval_framework_docker"], flush=True)
    out["eval_framework_process"] = run_reference(exs, "process")
    print("process", out["eval_framework_process"], flush=True)
    out["verifier_direct"] = run_direct(exs)
    print("direct", out["verifier_direct"], flush=True)
    workers = int(config.load("service.json")["workers"])
    out["verifier_service"] = run_service(exs, workers)
    print("service", out["verifier_service"], flush=True)
    out["lanes"] = {"separate": run_lanes(exs, workers, True), "shared_fifo": run_lanes(exs, workers, False)}
    print("lanes", out["lanes"], flush=True)
    (ROOT / "reports/throughput.json").write_text(json.dumps(out, indent=2) + "\n")
