"""Builds every published number from the files in reports/: reports/results.json (and the dashboard's copy),
REPORT.md, and the results block in README.md. Run after the sweeps and the throughput benchmark."""

from __future__ import annotations

import inspect
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

import attacks_dev
import attacks_heldout

from crv.datasets import humaneval, mbpp

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "reports"


def load(name: str) -> Any:
    p = R / name
    return json.loads(p.read_text()) if p.exists() else None


def rows(name: str) -> list[dict[str, Any]]:
    p = R / name
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def test_count() -> int:
    out = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "pytest", "--collect-only", "-q", "-p", "no:warnings"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    # with the project's addopts (-q) plus -q, pytest prints "tests/file.py: N" per file
    return sum(int(line.rsplit(":", 1)[1]) for line in out.splitlines() if line.startswith("tests/") and ": " in line)


def main() -> None:
    order = [e.task.id for e in humaneval() + mbpp()]
    example = next(e for e in mbpp() if e.task.id == "MBPP/2")
    attacks: list[dict[str, Any]] = []
    for which, module in (("dev", attacks_dev), ("heldout", attacks_heldout)):
        rs = rows(f"sweep-{which}.jsonl")
        patched = load(f"sweep-{which}-patched-summary.json")
        summary = load(f"sweep-{which}-summary.json") or {"attacks": {}}
        for name, fn in module.ATTACKS.items():
            by_task = {r["task"]: r for r in rs if r["kind"] == name}
            if not by_task:
                continue
            s = summary["attacks"][name]
            attacks.append(
                {
                    "name": name,
                    "set": which,
                    "description": inspect.getdoc(fn) or "",
                    "completions": s["completions"],
                    "eval_framework_passed": s["eval_framework_passed"],
                    "patched_passed": patched["attacks"][name]["eval_framework_passed"] if patched else None,
                    "ours_passed": s["ours_passed"],
                    "ours_outcomes": s["ours_outcomes"],
                    "ef_mask": "".join("1" if by_task[t]["eval_framework"] is True else "0" for t in order),
                    "ours_mask": "".join("1" if by_task[t]["ours"] == "passed" else "0" for t in order),
                    "example": {"task": example.task.id, "completion": fn(example)},
                }
            )
    dev = load("sweep-dev-summary.json")["honest"]
    first = load("sweep-dev-first-run-summary.json")["honest"]
    split = dev["trust"].get("split", 0) / max(1, sum(dev["trust"].values()))
    tp = load("throughput.json") or {}
    eq = load("runtime-equivalence.json")
    up = load("upstream-tests.json")
    up_honest = load("upstream-honest.json")
    pin = load("heldout-pin.json")
    closes = [a["name"] for a in attacks if a["patched_passed"] == 0]
    open_ = [a["name"] for a in attacks if a["patched_passed"]]
    workers = tp.get("verifier_service", {}).get("workers", 2)
    throughput = []
    if tp:
        d, p, v, s = (
            tp["eval_framework_docker"],
            tp["eval_framework_process"],
            tp["verifier_direct"],
            tp["verifier_service"],
        )
        throughput = [
            {
                "label": "eval-framework, Docker sandbox",
                "per_s": round(1000 / d["latency_ms"]["p50"], 1),
                "p50_ms": d["latency_ms"]["p50"],
                "p99_ms": None,
                "note": f"llm-sandbox pool, one sample at a time as the metric runs, warm containers (rate from p50). "
                f"Containers are recycled every 100 samples; on this host that took ~90 s, so {d['completions']} "
                f"samples ran at {d['per_s']}/s overall",
            },
            {
                "label": "eval-framework, fresh process",
                "per_s": p["per_s"],
                "p50_ms": p["latency_ms"]["p50"],
                "p99_ms": p["latency_ms"]["p99"],
                "note": "same programs and scoring, one new Python process per sample",
            },
            {
                "label": "This verifier, one worker",
                "per_s": v["per_s"],
                "p50_ms": v["latency_ms"]["p50"],
                "p99_ms": v["latency_ms"]["p99"],
                "note": "zygote fork per sample, tests outside the sandbox",
            },
            {
                "label": f"This verifier, service with {workers} workers",
                "per_s": s["per_s"],
                "p50_ms": None,
                "p99_ms": None,
                "note": "HTTP batch API, batches of 64",
            },
        ]
    lanes = tp.get("lanes", {})
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()
    results = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "commit": commit,
        "host": f"{os.cpu_count()} vCPU, Linux {platform.release().split('-')[0]}, Python {platform.python_version()}",
        "tests": test_count(),
        "attacks": attacks,
        "honest": {
            "completions": dev["completions"],
            "agree": dev["agree"],
            "agreement": dev["agreement"],
            "first_run_agreement": first["agreement"],
            "disagreements": [
                {"task": x["task"], "kind": x["kind"], "reason": x["reason"]} for x in dev["disagreements"]
            ],
            "split_share": round(split, 4),
            "reference_passed": dev["reference_passed"],
        },
        "equivalence": {"sample": eq["sample"], "same": eq["same"]},
        "throughput": throughput,
        "lanes": {
            "burst": lanes.get("separate", {}).get("rl_burst", 0),
            "shared_p50_ms": lanes.get("shared_fifo", {}).get("eval_queue_wait_ms", {}).get("p50", 0),
            "shared_p99_ms": lanes.get("shared_fifo", {}).get("eval_queue_wait_ms", {}).get("p99", 0),
            "separate_p50_ms": lanes.get("separate", {}).get("eval_queue_wait_ms", {}).get("p50", 0),
            "separate_p99_ms": lanes.get("separate", {}).get("eval_queue_wait_ms", {}).get("p99", 0),
        },
        "upstream": {
            "fixed": len(up["fixed"]),
            "new_failures": len(up["new_failures"]),
            "closes": closes,
            "leaves_open": open_,
            "honest_scores_changed": up_honest["scores_changed_by_patch"] if up_honest else None,
            "honest_checked": up_honest["honest_completions"] if up_honest else None,
        },
        "heldout": {"sha256": pin["sha256"], "pinned_at": pin["pinned_at"]} if pin else None,
    }
    text = json.dumps(results, indent=1) + "\n"
    (R / "results.json").write_text(text)
    (ROOT / "dashboard/public/results.json").write_text(text)
    (ROOT / "REPORT.md").write_text(report_md(results, tp))
    readme = ROOT / "README.md"
    if readme.exists():
        t = readme.read_text()
        start, end = "<!-- results:start -->", "<!-- results:end -->"
        if start in t:
            t = t[: t.index(start)] + start + "\n" + results_block(results) + end + t[t.index(end) + len(end) :]
            readme.write_text(t)
    print(f"wrote reports/results.json, REPORT.md ({len(attacks)} attacks, {results['tests']} tests)")


def results_block(r: dict[str, Any]) -> str:
    lines = ["| Attack | Set | eval-framework | eval-framework + patch | This verifier |", "|---|---|--:|--:|--:|"]
    for a in r["attacks"]:
        p = "" if a["patched_passed"] is None else f"{a['patched_passed']:,}"
        lines.append(
            f"| {a['name'].replace('_', ' ')} | {'held-out' if a['set'] == 'heldout' else 'development'} | "
            f"{a['eval_framework_passed']:,} | {p} | **{a['ours_passed']:,}** |"
        )
    total = sum(a["completions"] for a in r["attacks"])
    ef = sum(a["eval_framework_passed"] for a in r["attacks"])
    pa = sum(a["patched_passed"] or 0 for a in r["attacks"])
    ours = sum(a["ours_passed"] for a in r["attacks"])
    lines.append(f"| **All {len(r['attacks'])}** | | **{ef:,} of {total:,}** | **{pa:,}** | **{ours:,}** |")
    h = r["honest"]
    lines += [
        "",
        "Each cell counts the attack completions scored as a pass, one per task (1,138 HumanEval and MBPP tasks).",
        "",
    ]
    lines += [
        "| Check | Result |",
        "|---|---|",
        f"| Honest completions, verdicts equal to eval-framework's | {h['agreement'] * 100:.2f}% ({h['agree']:,} of {h['completions']:,}); first run {h['first_run_agreement'] * 100:.2f}% |",
        f"| Honest completions verified with the tests outside the sandbox | {h['split_share'] * 100:.1f}% |",
        f"| eval-framework's Docker path vs the isolated process runtime used in the sweeps | same score on {r['equivalence']['same']} of {r['equivalence']['sample']} |",
    ]
    for t in r["throughput"]:
        lat = f", p50 {t['p50_ms']} ms" if t["p50_ms"] is not None else ""
        lat += f", p99 {t['p99_ms']} ms" if t["p99_ms"] is not None else ""
        lines.append(f"| Throughput: {t['label']} | {t['per_s']:,} completions/s{lat} |")
    ln = r["lanes"]
    if ln["burst"]:
        lines.append(
            f"| Queue wait of an eval request during a {ln['burst']:,}-completion RL burst, p99 | "
            f"{ln['shared_p99_ms'] / 1000:.1f} s in one shared queue, {ln['separate_p99_ms']:,.0f} ms with its own lane |"
        )
    u = r["upstream"]
    lines.append(
        f"| Upstream patch for eval-framework | {u['fixed']} failing tests fixed, {u['new_failures']} broken, "
        f"{u['honest_scores_changed']} of {u['honest_checked']:,} honest scores changed |"
    )
    lines.append(f"| Tests in this repository | {r['tests']} |")
    lines.append("")
    lines.append(
        f"<sub>Generated by `python bench/report.py` from `reports/` ({r['generated_at']}, commit {r['commit']}, {r['host']}).</sub>"
    )
    return "\n".join(lines) + "\n"


def report_md(r: dict[str, Any], tp: dict[str, Any]) -> str:
    out = [
        f"# Results\n\nGenerated by `python bench/report.py` on {r['generated_at']} at commit {r['commit']} ({r['host']}). "
        "Do not edit by hand.\n",
        "## Summary\n",
        results_block(r),
        "## Attacks\n",
    ]
    for a in r["attacks"]:
        out.append(f"### {a['name'].replace('_', ' ')} ({'held-out' if a['set'] == 'heldout' else 'development'})\n")
        out.append(a["description"] + "\n")
        out.append(
            f"eval-framework passed {a['eval_framework_passed']:,} of {a['completions']:,}"
            + ("" if a["patched_passed"] is None else f"; with the patch {a['patched_passed']:,}")
            + f"; this verifier {a['ours_passed']:,} ({', '.join(f'{v:,} {k}' for k, v in a['ours_outcomes'].items())}).\n"
        )
        out.append(f"```python\n# {a['example']['task']}\n{a['example']['completion']}```\n")
    h = r["honest"]
    out.append("## Honest completions\n")
    out.append(
        f"{h['completions']:,} completions: each task's reference solution and up to two mutants of it. Agreement "
        f"{h['agreement'] * 100:.2f}%. Reference solutions passed: eval-framework {h['reference_passed']['eval_framework']:,}, "
        f"this verifier {h['reference_passed']['ours']:,}, of {h['reference_passed']['of']:,}.\n"
    )
    if h["disagreements"]:
        out.append("| Task | Completion | This verifier's reason |\n|---|---|---|")
        out += [f"| {d['task']} | {d['kind']} | {d['reason']} |" for d in h["disagreements"]]
        out.append("")
    if tp:
        out.append(
            "## Throughput\n\n```json\n"
            + json.dumps({k: v for k, v in tp.items() if k != "lanes"}, indent=1)
            + "\n```\n\n## Lanes\n\n```json\n"
            + json.dumps(tp.get("lanes", {}), indent=1)
            + "\n```\n"
        )
    return "\n".join(out)


if __name__ == "__main__":
    main()
