"""What a verification concludes, and what it is worth as a reward."""

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class Outcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"  # a test failed, the completion raised, or returned something that is not data
    TIMEOUT = "timeout"  # wall or CPU limit
    RESOURCE = "resource"  # memory, output or process limit
    CRASHED = "crashed"  # the completion's process ended before the tests finished (e.g. os._exit)
    INFRA_ERROR = "infra_error"  # the verifier failed; says nothing about the completion


class Trust(StrEnum):
    # Tests ran outside the sandbox; the completion never saw them or the expected values.
    SPLIT = "split"
    # Tests had to run inside the sandbox (unittest suites with mocks). Results came over a structured
    # channel, never from stdout, but a deliberate in-process attack is only detected, not made impossible.
    IN_SANDBOX = "in_sandbox"


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    reason: str
    trust: Trust
    calls: int = 0
    wall_ms: float = 0.0
    # Things worth knowing that do not change the outcome by themselves, e.g. "early_exit", "wrote_stdout".
    signals: tuple[str, ...] = ()

    @property
    def reward(self) -> float | None:
        if self.outcome is Outcome.INFRA_ERROR:
            return None
        return 1.0 if self.outcome is Outcome.PASSED else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {
            "outcome": self.outcome.value,
            "reward": self.reward,
            "reason": self.reason,
            "trust": self.trust.value,
            "calls": d["calls"],
            "wall_ms": d["wall_ms"],
            "signals": list(self.signals),
        }
