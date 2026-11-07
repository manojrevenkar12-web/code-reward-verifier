"""HTTP API.

POST /v1/verify   {"lane": "rl", "items": [{"task": {...}, "completion": "..."}]}  →  {"verdicts": [...]}
GET  /healthz     workers, isolation, queue depth
GET  /metrics     Prometheus text format
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from crv import config
from crv.domain.policy import ResourcePolicy, load_policies
from crv.service.lanes import LaneFull, LaneScheduler
from crv.service.pool import WorkerPool
from crv.service.schema import BadRequest, submission_from


def create_app(settings: dict[str, Any] | None = None, policies: dict[str, ResourcePolicy] | None = None) -> Starlette:
    s = settings or config.load("service.json")
    policies = policies or load_policies()
    lanes = s["lanes"]
    scheduler = LaneScheduler({k: v["weight"] for k, v in lanes.items()}, {k: v["capacity"] for k, v in lanes.items()})
    pool = WorkerPool(s["workers"], s["slot_base"], policies, scheduler, s["watchdog_grace_s"])
    outcomes: Counter[tuple[str, str]] = Counter()

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        await pool.start()
        if s["require_full_isolation"] and not all(pool.isolation.values()):
            await pool.close()
            raise RuntimeError(f"incomplete isolation {pool.isolation}; run as root or set require_full_isolation")
        yield
        await pool.close()

    async def verify(request: Request) -> JSONResponse:
        body = await request.body()
        if len(body) > s["max_body_bytes"]:
            return JSONResponse({"error": "body too large"}, status_code=413)
        try:
            data = json.loads(body)
            lane = data.get("lane", "eval")
            if lane not in policies or lane not in lanes:
                raise BadRequest(f"unknown lane {lane!r}")
            items = data.get("items")
            if not isinstance(items, list) or not 0 < len(items) <= s["max_items_per_request"]:
                raise BadRequest(f"items must be a list of 1 to {s['max_items_per_request']} submissions")
            subs = [submission_from(i) for i in items]
        except (ValueError, BadRequest) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        free = lanes[lane]["capacity"] - scheduler.depth()[lane]
        if len(subs) > free:
            return JSONResponse(
                {"error": f"lane {lane} is full", "retry_after_s": 1}, status_code=429, headers={"Retry-After": "1"}
            )
        try:
            futures = [pool.verify(sub, lane) for sub in subs]
        except LaneFull:
            return JSONResponse({"error": f"lane {lane} is full"}, status_code=429, headers={"Retry-After": "1"})
        verdicts = await asyncio.gather(*futures)
        for v in verdicts:
            outcomes[(lane, v["outcome"])] += 1
        return JSONResponse({"verdicts": verdicts})

    async def healthz(request: Request) -> JSONResponse:
        alive = sum(w.proc.is_alive() for w in pool.workers)
        return JSONResponse(
            {
                "status": "ok" if alive == pool.size else "degraded",
                "workers": pool.size,
                "alive": alive,
                "isolation": pool.isolation,
                "queued": scheduler.depth(),
            }
        )

    async def metrics(request: Request) -> PlainTextResponse:
        lines = ["# TYPE crv_verifications_total counter"]
        lines += [
            f'crv_verifications_total{{lane="{ln}",outcome="{o}"}} {n}' for (ln, o), n in sorted(outcomes.items())
        ]
        lines.append("# TYPE crv_queue_depth gauge")
        lines += [f'crv_queue_depth{{lane="{ln}"}} {n}' for ln, n in scheduler.depth().items()]
        lines.append("# TYPE crv_workers_replaced_total counter")
        lines.append(f"crv_workers_replaced_total {pool.replaced}")
        return PlainTextResponse("\n".join(lines) + "\n")

    return Starlette(
        routes=[Route("/v1/verify", verify, methods=["POST"]), Route("/healthz", healthz), Route("/metrics", metrics)],
        lifespan=lifespan,
    )
