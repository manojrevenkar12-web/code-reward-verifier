"""Score every variant with eval-framework and with this verifier, side by side.

    python bench/sweep.py --set dev        # honest variants + development attacks
    python bench/sweep.py --set heldout    # pinned held-out attacks, once
    python bench/sweep.py --equivalence 150  # docker vs process runtime for eval-framework, on a random sample

Writes reports/sweep-<set>.jsonl (one line per completion) and reports/sweep-<set>-summary.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import random
import sys
import time
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from variants import Variant, honest

from crv.datasets import Example, humaneval, mbpp
from crv.domain.policy import load_policies
from crv.domain.task import Submission

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
_worker: dict[str, Any] = {}


def examples() -> list[Example]:
    return humaneval() + mbpp()


def variants(which: str) -> list[Variant]:
    out: list[Variant] = []
    if which == "dev":
        from attacks_dev import ATTACKS
    else:
        from attacks_heldout import ATTACKS
    for ex in examples():
        if which == "dev":
            out.extend(honest(ex))
        for name, attack in ATTACKS.items():
            out.append(Variant(ex, name, attack(ex)))
    return out


def _init(slot: int) -> None:
    import reference

    from crv.sandbox.zygote import ZygoteClient
    from crv.verify.verifier import Verifier

    ident = mp.current_process()._identity
    z = ZygoteClient.start(slot=slot + (ident[0] if ident else 0))
    # Same 10 s ceiling as the reference scorer in the sweeps (see reference.TIMEOUT_S).
    policy = replace(load_policies()["eval"], wall_s=reference.TIMEOUT_S, cpu_s=reference.TIMEOUT_S - 0.5)
    _worker.update(reference=reference, verifier=Verifier(z), policy=policy)


def _score(item: tuple[int, str, str, str]) -> dict[str, Any]:
    index, task_id, kind, completion = item
    ex = _BY_ID[task_id]
    ref = _worker["reference"]
    t0 = time.perf_counter()
    theirs = ref.score(ref.program(ex, completion), "process")
    t1 = time.perf_counter()
    if EF_ONLY:
        return {
            "i": index,
            "task": task_id,
            "kind": kind,
            "eval_framework": theirs,
            "ours": None,
            "trust": None,
            "reason": "",
            "signals": [],
            "ef_ms": round((t1 - t0) * 1000, 1),
            "ours_ms": 0.0,
        }
    v = _worker["verifier"].verify(Submission(ex.task, completion), _worker["policy"])
    return {
        "i": index,
        "task": task_id,
        "kind": kind,
        "eval_framework": theirs,
        "ours": v.outcome.value,
        "trust": v.trust.value,
        "reason": v.reason[:200],
        "signals": list(v.signals),
        "ef_ms": round((t1 - t0) * 1000, 1),
        "ours_ms": v.wall_ms,
    }


_BY_ID: dict[str, Example] = {}
EF_ONLY = False  # --patched: score attacks with the patched eval-framework only (PYTHONPATH=<patched>/src)


def sweep(which: str, processes: int, label: str = "") -> None:
    if which == "heldout" and not label:
        pin = json.loads((REPORTS / "heldout-pin.json").read_text())
        digest = hashlib.sha256((ROOT / "bench/attacks_heldout.py").read_bytes()).hexdigest()
        if digest != pin["sha256"]:
            raise SystemExit("bench/attacks_heldout.py changed after it was pinned; refusing to run")
        if (REPORTS / "sweep-heldout.jsonl").exists():
            raise SystemExit("the held-out set has already run (reports/sweep-heldout.jsonl exists)")
    vs = variants(which)
    if EF_ONLY:
        vs = [v for v in vs if v.kind not in ("reference", "mutant")]
    _BY_ID.update({v.example.task.id: v.example for v in vs})
    items = [(i, v.example.task.id, v.kind, v.completion) for i, v in enumerate(vs)]
    t0 = time.time()
    rows: list[dict[str, Any]] = []
    with mp.get_context("fork").Pool(processes, initializer=_init, initargs=(40,)) as pool:
        for n, row in enumerate(pool.imap_unordered(_score, items, chunksize=8), 1):
            rows.append(row)
            if n % 500 == 0:
                print(f"{n}/{len(items)} in {time.time() - t0:.0f} s", flush=True)
    rows.sort(key=lambda r: r["i"])
    name = f"sweep-{which}{label}"
    with open(REPORTS / f"{name}.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    summary = summarise(rows)
    summary["wall_s"] = round(time.time() - t0, 1)
    (REPORTS / f"{name}-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary["attacks"], indent=1))
    if "honest" in summary:
        print(json.dumps(summary["honest"], indent=1))


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_kind[r["kind"]].append(r)
    out: dict[str, Any] = {"attacks": {}}
    for kind, rs in by_kind.items():
        if kind in ("reference", "mutant"):
            continue
        out["attacks"][kind] = {
            "completions": len(rs),
            "eval_framework_passed": sum(r["eval_framework"] is True for r in rs),
            "ours_passed": sum(r["ours"] == "passed" for r in rs),
            "ours_outcomes": dict(Counter(str(r["ours"]) for r in rs)),
        }
    honest_rows = by_kind.get("reference", []) + by_kind.get("mutant", [])
    if honest_rows:
        scored = [r for r in honest_rows if r["eval_framework"] is not None]
        agree = [r for r in scored if (r["ours"] == "passed") == r["eval_framework"]]
        out["honest"] = {
            "completions": len(honest_rows),
            "eval_framework_infra_errors": len(honest_rows) - len(scored),
            "agree": len(agree),
            "agreement": round(len(agree) / max(1, len(scored)), 4),
            "reference_passed": {
                "eval_framework": sum(r["eval_framework"] is True for r in by_kind["reference"]),
                "ours": sum(r["ours"] == "passed" for r in by_kind["reference"]),
                "of": len(by_kind["reference"]),
            },
            "trust": dict(Counter(r["trust"] for r in honest_rows)),
            "disagreements": [
                {k: r[k] for k in ("task", "kind", "eval_framework", "ours", "reason")}
                for r in scored
                if (r["ours"] == "passed") != r["eval_framework"]
            ],
        }
    return out


def equivalence(n: int) -> None:
    """Does the process runtime give the same eval-framework scores as their real Docker path?"""
    import reference

    vs = variants("dev")
    random.Random(7).shuffle(vs)
    sample = vs[:n]
    same = 0
    rows = []
    t0 = time.time()
    for v in sample:
        prog = reference.program(v.example, v.completion)
        d = reference.score(prog, "docker")
        p = reference.score(prog, "process")
        same += d == p
        rows.append({"task": v.example.task.id, "kind": v.kind, "docker": d, "process": p})
    result = {
        "sample": n,
        "same": same,
        "seed": 7,
        "wall_s": round(time.time() - t0, 1),
        "differences": [r for r in rows if r["docker"] != r["process"]],
    }
    (REPORTS / "runtime-equivalence.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("sample", "same", "wall_s")}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["dev", "heldout"])
    ap.add_argument("--processes", type=int, default=2)
    ap.add_argument("--equivalence", type=int)
    ap.add_argument("--patched", action="store_true", help="eval-framework only, from the patched checkout")
    a = ap.parse_args()
    if a.equivalence:
        equivalence(a.equivalence)
    elif a.patched:
        import eval_framework

        if "ef-patch" not in eval_framework.__file__:
            raise SystemExit("set PYTHONPATH to the patched eval-framework's src")
        EF_ONLY = True
        sweep(a.set, a.processes, label="-patched")
    else:
        sweep(a.set, a.processes)
