"""Resource limits for one verification. Values come from config/policy.json, per lane."""

from dataclasses import dataclass

from crv import config


@dataclass(frozen=True)
class ResourcePolicy:
    wall_s: float
    cpu_s: float
    memory_mb: int
    max_output_bytes: int
    max_processes: int
    max_calls: int

    def __post_init__(self) -> None:
        if not 0 < self.cpu_s <= self.wall_s:
            raise ValueError("need 0 < cpu_s <= wall_s")
        if self.memory_mb < 16 or self.max_processes < 1 or self.max_calls < 1 or self.max_output_bytes < 0:
            raise ValueError("limits must be positive")


def load_policies() -> dict[str, ResourcePolicy]:
    lanes = config.load("policy.json")["lanes"]
    return {name: ResourcePolicy(**values) for name, values in lanes.items()}
