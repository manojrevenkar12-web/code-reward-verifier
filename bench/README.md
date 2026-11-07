# Benchmarks

Everything in `reports/`, `REPORT.md`, the README's results block and the dashboard data comes from these scripts.

## Reference environment (eval-framework, unchanged)

```bash
git clone https://github.com/Aleph-Alpha-Research/eval-framework /tmp/aa/eval-framework
git -C /tmp/aa/eval-framework checkout f096d6802a7dc92f2a5bbd08147a218a7472d40a
python3 -m venv /tmp/aa/ve && /tmp/aa/ve/bin/pip install -e /tmp/aa/eval-framework -e .   # eval-framework + this package
```

eval-framework runs code in Docker through llm-sandbox. The benchmark host could not reach Docker Hub, so its
Docker baseline uses an image built from the host's own Python 3.13 (`crv/python-host:3.13`). On a random sample
of 150 programs, the isolated plain-process runtime used for the large sweeps gave the same eval-framework score as
that Docker path every time (`reports/runtime-equivalence.json`).

## Runs (as root: the verifier needs namespaces)

```bash
python bench/sweep.py --equivalence 150      # Docker vs process runtime for eval-framework
python bench/sweep.py --set dev              # honest variants + development attacks, both scorers
python bench/sweep.py --set heldout          # pinned held-out attacks; refuses a second run or a changed file
PYTHONPATH=<patched eval-framework>/src python bench/sweep.py --set dev --patched      # eval-framework + upstream patch
PYTHONPATH=<patched eval-framework>/src python bench/sweep.py --set heldout --patched
PYTHONPATH=<patched eval-framework>/src python bench/upstream_honest.py                # patch vs honest scores
python bench/throughput.py                   # throughput + lanes (then --lanes-only to redo the lanes part)
python bench/report.py                       # results.json, REPORT.md, README block, dashboard data
```

## What is compared

- **Completions per task.** The dataset's reference solution, up to two textual mutants of it (`variants.py`), and
  one completion per attack. HumanEval has 164 tasks and MBPP 974.
- **Development attacks** (`attacks_dev.py`) were used while building the verifier. **Held-out attacks**
  (`attacks_heldout.py`) were written, pinned by sha256 in `reports/heldout-pin.json` and committed before their
  only run.
- **Time limit.** eval-framework allows 60 s per program. The sweeps use 10 s for both scorers, which only matters for
  programs that never finish (mutants with an endless loop); those fail under either limit.
- **Lanes.** One request with 2,000 RL completions, and one eval request every 100 ms while it runs. Queue wait comes
  from the service's own `queue_ms` field. End-to-end latency also includes the eval task itself (MBPP/123 takes
  3.7 s to verify on its own).
