# 3. In-sandbox mode is explicit, and a lane can refuse it

**Status:** accepted

## Context

Some tests cannot run outside the sandbox:
- unittest modules that patch the completion's imports;
- tests that pass a lambda;
- tests that build objects from the completion's own classes.

In the sandbox, the completion shares a process with the tests. It can be detected tampering with the test
machinery, but it cannot be prevented from doing so.

## Decision

These tests run inside the sandbox and report one record per test over a structured channel. A pass needs:
- a success record for every test id read from the test source's syntax tree;
- the test machinery (`TestCase.run`, the assert methods, `isinstance`, `os._exit`) unchanged by identity since
  before the completion loaded;
- the run to finish before the channel closes.

Such verdicts carry `trust: in_sandbox`. A lane can set `allow_in_sandbox: false`. The `rl` lane does by default:
those tasks then come back as `infra_error` with no reward, so a trainer drops them instead of training on a
check that can be gamed.

## Consequences

- Eval runs keep full coverage.
- RL runs only learn from verdicts the policy cannot forge.
- The share of tasks that need in-sandbox mode is measured (it is small on HumanEval and MBPP; see REPORT.md).
