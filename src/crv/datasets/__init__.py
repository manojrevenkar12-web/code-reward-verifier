"""HumanEval and MBPP as tasks, with their reference solutions as honest completions."""

import ast
import gzip
import json
from dataclasses import dataclass

from crv.config import ROOT
from crv.domain.task import AssertList, CheckFunction, Task


@dataclass(frozen=True)
class Example:
    task: Task
    reference: str  # the dataset's own solution, as a completion
    raw: dict[str, object]  # the original row, for adapters that rebuild a framework's program


def humaneval() -> list[Example]:
    with gzip.open(ROOT / "data/humaneval/HumanEval.jsonl.gz", "rt") as f:
        rows = [json.loads(line) for line in f]
    return [
        Example(
            Task(r["task_id"], prompt=r["prompt"], tests=CheckFunction(r["test"], r["entry_point"])),
            r["canonical_solution"],
            r,
        )
        for r in rows
    ]


def mbpp() -> list[Example]:
    with open(ROOT / "data/mbpp/mbpp.jsonl") as f:
        rows = [json.loads(line) for line in f]
    return [
        Example(
            Task(
                f"MBPP/{r['task_id']}", prompt="", tests=AssertList(tuple(r["test_list"]), setup=r["test_setup_code"])
            ),
            r["code"],
            r,
        )
        for r in rows
    ]


def mbpp_function_name(task: Task) -> str:
    """The function an MBPP task's asserts call (the first call in the first assert)."""
    assert isinstance(task.tests, AssertList)
    for node in ast.walk(ast.parse(task.tests.asserts[0])):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return node.func.id
    raise ValueError(f"{task.id}: no function call in the first assert")
