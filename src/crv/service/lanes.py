"""Weighted fair scheduling across lanes (smooth weighted round-robin over the lanes that have work)."""

from collections import deque
from typing import Any


class LaneFull(Exception):
    """The lane's queue is at capacity; the caller should retry later (HTTP 429)."""


class LaneScheduler:
    def __init__(self, weights: dict[str, int], capacity: dict[str, int]) -> None:
        if not weights or any(w <= 0 for w in weights.values()):
            raise ValueError("weights must be positive")
        self.weights = dict(weights)
        self.capacity = dict(capacity)
        self.queues: dict[str, deque[Any]] = {lane: deque() for lane in weights}
        self.current = dict.fromkeys(weights, 0)

    def push(self, lane: str, item: Any) -> None:
        q = self.queues[lane]
        if len(q) >= self.capacity[lane]:
            raise LaneFull(lane)
        q.append(item)

    def pop(self) -> tuple[str, Any] | None:
        active = [lane for lane, q in self.queues.items() if q]
        if not active:
            return None
        total = sum(self.weights[lane] for lane in active)
        for lane in active:
            self.current[lane] += self.weights[lane]
        best = max(active, key=lambda lane: self.current[lane])
        self.current[best] -= total
        return best, self.queues[best].popleft()

    def depth(self) -> dict[str, int]:
        return {lane: len(q) for lane, q in self.queues.items()}

    def __len__(self) -> int:
        return sum(len(q) for q in self.queues.values())
