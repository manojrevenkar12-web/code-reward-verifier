"""Does the upstream patch change eval-framework's score on any honest completion?

PYTHONPATH=<patched eval-framework>/src python bench/upstream_honest.py
"""

import json
import multiprocessing as mp
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import reference
from variants import Variant, honest

from crv.datasets import humaneval, mbpp

REPORTS = Path(__file__).resolve().parents[1] / "reports"


def score(v: Variant) -> bool | None:
    return reference.score(reference.program(v.example, v.completion), "process")


def main() -> None:
    if "ef-patch" not in reference.code_assertion.__file__:
        raise SystemExit("set PYTHONPATH to the patched eval-framework's src")
    items = [v for ex in humaneval() + mbpp() for v in honest(ex)]
    unpatched: dict[tuple[str, str], list[bool | None]] = {}
    with open(REPORTS / "sweep-dev.jsonl") as f:
        for line in f:
            r = json.loads(line)
            if r["kind"] in ("reference", "mutant"):
                unpatched.setdefault((r["task"], r["kind"]), []).append(r["eval_framework"])
    with mp.get_context("fork").Pool(2) as pool:
        scores = pool.map(score, items, chunksize=16)
    patched: dict[tuple[str, str], list[bool | None]] = {}
    for v, s in zip(items, scores, strict=True):
        patched.setdefault((v.example.task.id, v.kind), []).append(s)
    changed = [k for k in patched if patched[k] != unpatched.get(k)]
    out = {
        "honest_completions": len(items),
        "scores_changed_by_patch": len(changed),
        "changed": [list(k) for k in changed][:20],
    }
    (REPORTS / "upstream-honest.json").write_text(json.dumps(out, indent=2) + "\n")
    print(out)


if __name__ == "__main__":
    main()
