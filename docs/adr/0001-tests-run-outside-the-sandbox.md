# 1. The tests run outside the sandbox

**Status:** accepted

## Context

Code benchmarks are usually scored by running one program, the completion followed by the tests, and reading
what it prints. In eval-framework, MBPP and HumanEval pass when the last word of the output is `True`, and
BigCodeBench passes when the output contains `OK` and not `FAILED`. The completion runs before the tests, in the
same process, and the program file holds the expected values. So a completion can print the marker and exit,
replace `print`, return an object that equals everything, or read the answers from its own file.

During evaluation this rarely matters, because the model was not trained against the checker. Under
reinforcement learning, the reward is the checker, and a policy finds these holes.

## Decision

Split the program in two:
- The completion runs in the sandbox and never receives the tests.
- The tests run in the verifier's trusted process. Every function the completion defines becomes a proxy. A call
  sends the arguments into the sandbox and gets back the result as plain data: exact built-in types, decoded
  under a size budget.
- Nothing is read from the completion's stdout.

## Consequences

- The completion sees inputs only. It cannot print its way to a pass, read the expected values, or hand back
  an object with its own `__eq__` (such a value does not decode).
- Tests that need the completion in-process cannot be split this way: unittest modules with mocks, or tests that
  pass a lambda or construct the completion's own classes. These run in the sandbox (ADR 3).
- One more inter-process round trip per call (tens of microseconds).
