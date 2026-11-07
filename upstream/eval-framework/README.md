# A fix for eval-framework's execution scoring

Two patches against [Aleph-Alpha-Research/eval-framework](https://github.com/Aleph-Alpha-Research/eval-framework)
at `f096d68`, in the repository's own style (tests first, conventional commits):

1. `0001-test-…`: tests that fail on the current code.
   - Five completions that print `True` without solving the task (print then `os._exit`, print then `sys.exit`,
     replacing `print`, printing at exit, writing to fd 1) score 1.0 in `CodeCompletionAssertion`.
   - Unittest output that only contains the letters `OK` (`KeyError: 'FACEBOOK'`, `No module named 'BOOKSHELF'`,
     `Ran 0 tests … OK`) is parsed as a pass by `_parse_unittest_output`, which BigCodeBench uses.
2. `0002-fix-…`: the fix, 26 lines added and 20 removed.
   - `CodeCompletionAssertion` appends one line that prints a token generated at scoring time. A pass is `True`
     followed by that token.
   - `_parse_unittest_output` only accepts unittest's own summary: `Ran N tests` with N > 0, then `OK`, and no
     `FAILED`.

Apply and test:

```bash
git clone https://github.com/Aleph-Alpha-Research/eval-framework && cd eval-framework
git checkout f096d68 && git am ../upstream/eval-framework/000*.patch
uv run pytest tests/tests_eval_framework/metrics/test_code_assertion.py tests/tests_eval_framework/tasks/test_utils.py
```

**Results** (`reports/upstream-tests.json`). On the four affected test files:
- the patch fixes the 11 new tests;
- it breaks none: the set of failing tests is otherwise identical before and after;
- the failures left on both sides need Hugging Face or Docker Hub, which the benchmark host cannot reach.

**What the patch does not close.** These need the tests to run outside the completion's process, which is what
this repository does:
- returning an object that equals everything;
- reading the expected values out of the program file;
- rewriting `False` to `True` in `sys.stdout`.

The sweeps measure all three, with and without the patch.
