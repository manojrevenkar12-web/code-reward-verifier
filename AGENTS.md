# Working in this repository

A verifier for code-execution rewards and coding evals: completions run in a hardened sandbox, the tests run outside
it, and verdicts are measured against eval-framework's own scoring.

## Fast feedback

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
ruff check src tests bench && ruff format --check src tests bench && mypy
sudo -E .venv/bin/python -m pytest        # ~106 tests, ~10 s; sandbox tests need root (namespaces, uid drop)
```

Benchmarks take minutes and need eval-framework installed: see `bench/README.md`.

## Invariants

1. **Nothing is parsed from a completion's output.** Results cross the sandbox boundary only as frames of the value
   codec (`crv/domain/values.py`). Only exact built-in and standard-library data types decode.
2. **The completion never receives the tests or the expected values** in split mode. Tests run on the trusted side.
   The completion's functions are proxies.
3. **The zygote holds no task data.** Sources and tests travel to each child over its own channel after the fork.
   Never pass them through the zygote's control socket.
4. **In-sandbox mode is explicit.** Its verdicts carry `trust: in_sandbox`, and a lane with `allow_in_sandbox: false`
   gets `infra_error` instead.
5. **Infra errors carry no reward** (`reward: null`). Never turn a verifier failure into a zero.
6. **Write tests first.** Every behaviour change starts with a failing test, in its own commit.
7. **Settings live in `config/`.** That covers lanes, limits, the sandbox and the service.
8. **Generated files are not edited by hand:** `reports/*.json`, `REPORT.md`, the README results block and
   `dashboard/public/results.json`. They come from `python bench/report.py`.

## The held-out rule

`bench/attacks_heldout.py` is pinned by sha256 in `reports/heldout-pin.json` and has run once. Do not edit it,
reformat it, or change the verifier to beat it. New attacks go in `attacks_dev.py`; a new held-out set needs a new pin.
