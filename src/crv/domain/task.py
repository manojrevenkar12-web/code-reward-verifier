"""Tasks and their tests. Tests are trusted (they come from the dataset); completions are not."""

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class CheckFunction:
    """HumanEval style: `def check(candidate): assert ...`, called with the entry point."""

    source: str
    entry_point: str


@dataclass(frozen=True)
class AssertList:
    """MBPP style: assert statements that call the completion's function by name."""

    asserts: tuple[str, ...]
    setup: str = ""


@dataclass(frozen=True)
class UnittestModule:
    """BigCodeBench style: unittest.TestCase classes that need the completion in-process (mocks, files)."""

    source: str


TestSuite = CheckFunction | AssertList | UnittestModule


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str  # code that precedes the completion (HumanEval's signature and docstring); "" for MBPP
    tests: TestSuite


@dataclass(frozen=True)
class Submission:
    task: Task
    completion: str

    @property
    def solution_source(self) -> str:
        return self.task.prompt + self.completion


def expected_unittest_ids(source: str) -> tuple[str, ...]:
    """The tests a unittest module defines, read from its syntax tree. Success must be reported for each of
    these; a run that reports fewer (or prints "OK") is not a pass."""
    tree = ast.parse(source)
    ids: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {ast.unparse(b) for b in node.bases}
        if not bases & {"unittest.TestCase", "TestCase"}:
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name.startswith("test"):
                ids.append(f"{node.name}.{item.name}")
    return tuple(ids)
