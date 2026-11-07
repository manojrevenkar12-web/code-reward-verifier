"""eval-framework's own scoring, called through its own classes, for side-by-side comparison.

Needs eval-framework installed (see bench/README.md for the pinned commit). Nothing here re-implements their
logic: programs are built with their reconstruction functions and scored by their `CodeCompletionAssertion`
(HumanEval, MBPP) or their `_parse_unittest_output` (BigCodeBench's unittest parser).

Two runtimes execute the program:
- "docker": their real path, llm-sandbox's container pool, on a local image (registries are not reachable
  here; the image is this host's Python 3.13, see bench/README.md).
- "process": the same contract (stderr + stdout, stripped; empty output and timeouts raise the same
  exceptions) in a plain process, isolated with no network, an unprivileged uid and rlimits. Used for the
  large sweeps after `equivalence()` shows both runtimes give the same scores.
"""

from __future__ import annotations

import os
import resource
import subprocess
import sys
import tempfile
from typing import Any

import eval_framework.main  # noqa: F401  (import order: avoids a circular import inside eval_framework)
import eval_framework.metrics.completion.code_assertion as code_assertion
from eval_framework.benchmarks.humaneval import HumanEvalMetricContext, _reconstruct_program
from eval_framework.benchmarks.mbpp import evalplus_reconstruct, mbpp_ground_truth
from eval_framework.metrics.completion.code_assertion import CodeCompletionAssertion
from eval_framework.shared.types import Completion
from eval_framework.tasks.utils import (
    DockerReturnedEmptyOutput,
    _parse_unittest_output,
    run_python_code,
    unittest_merge_snippets,
)
from llm_sandbox.exceptions import SandboxTimeoutError

from crv.datasets import Example
from crv.domain.task import AssertList, CheckFunction

DOCKER_IMAGE = "crv/python-host:3.13"
_REAL_RUN = run_python_code


def program(example: Example, completion: str) -> str:
    """The exact program eval-framework would execute for this completion."""
    tests = example.task.tests
    if isinstance(tests, CheckFunction):
        ctx = HumanEvalMetricContext(test=tests.source, entry_point=tests.entry_point, prompt=example.task.prompt)
        return str(_reconstruct_program(completion, context=ctx, ground_truth=None, messages=[]))
    assert isinstance(tests, AssertList)
    return str(evalplus_reconstruct(completion, context=None, ground_truth=mbpp_ground_truth(example.raw), messages=[]))


def _isolate() -> None:
    os.unshare(os.CLONE_NEWNET)
    resource.setrlimit(resource.RLIMIT_AS, (512 * 2**20,) * 2)
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    os.setgroups([])
    os.setgid(65534)
    os.setuid(65534)


def process_runtime(code: str, image: Any = None, runtime_configs: Any = None, packages: Any = None,
                    timeout: int = 60, **_: Any) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, dir="/tmp") as f:
        f.write(code)
        path = f.name
    os.chmod(path, 0o644)
    try:
        r = subprocess.run([sys.executable, path], capture_output=True, text=True, errors="replace",
                           timeout=timeout, preexec_fn=_isolate, cwd="/tmp", env={"PATH": "/usr/bin:/bin"})
    except subprocess.TimeoutExpired:
        raise SandboxTimeoutError("timeout") from None
    finally:
        os.unlink(path)
    out = (r.stderr + r.stdout).strip()
    if not out.strip():
        raise DockerReturnedEmptyOutput("Docker returned an empty output.")
    return out


class _DockerAssertion(CodeCompletionAssertion):
    IMAGE = DOCKER_IMAGE


def score(prog: str, runtime: str = "process") -> bool | None:
    """eval-framework's verdict: True (pass), False (fail) or None (they record an infra error)."""
    code_assertion.run_python_code = process_runtime if runtime == "process" else _REAL_RUN  # type: ignore[assignment]
    metric = _DockerAssertion() if runtime == "docker" else CodeCompletionAssertion()
    resp = Completion.model_construct(error=None, completion=prog, id=0, subject="", ground_truth=None,
                                      messages=None, raw_completion=prog, raw_completion_num_tokens=None,
                                      prompt="", prompt_num_tokens=None, context=None)
    try:
        value = metric.calculate(resp)[0].value
    except Exception:
        return None
    return None if value is None else value == 1.0


def unittest_score(completion: str, test_module: str) -> bool:
    """BigCodeBench's scoring path: their merge function and their output parser."""
    try:
        out = process_runtime(unittest_merge_snippets(completion, test_module))
    except (DockerReturnedEmptyOutput, SandboxTimeoutError):
        return False
    return bool(_parse_unittest_output(out).success)
