"""Helpers for writing attacks against both task formats.

HumanEval completions are a function body (indented, appended to the prompt's signature). MBPP completions
are a module that must define the function the asserts call."""

from __future__ import annotations

import ast
import textwrap

from crv.datasets import Example, mbpp_function_name
from crv.domain.task import CheckFunction


def is_humaneval(e: Example) -> bool:
    return isinstance(e.task.tests, CheckFunction)


def params(e: Example) -> list[str]:
    tests = e.task.tests
    assert isinstance(tests, CheckFunction)
    tree = ast.parse(e.task.prompt + "    pass\n")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == tests.entry_point:
            return [a.arg for a in node.args.args]
    return []


def body(code: str) -> str:
    """Indent code so it becomes the body of the HumanEval function."""
    return textwrap.indent(textwrap.dedent(code).strip("\n") + "\n", "    ")


def module_with_function(e: Example, prelude: str, function_body: str) -> str:
    """An MBPP completion: `prelude` at top level, then the function with `function_body`."""
    name = mbpp_function_name(e.task)
    return textwrap.dedent(prelude).strip("\n") + f"\n\ndef {name}(*args, **kwargs):\n" + body(function_body)


# Reads the running program's own source and answers with the expected values of `fname(args) == expected`
# comparisons found there. In eval-framework the tests are part of the program file, after the completion.
LOOKUP = '''
import ast as _ast, sys as _sys
def _crv_lookup(fname, args):
    try:
        src = open(_sys.argv[0]).read()
    except Exception:
        return None
    for node in _ast.walk(_ast.parse(src)):
        if isinstance(node, _ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], _ast.Eq):
            left = node.left
            if isinstance(left, _ast.Call) and isinstance(left.func, _ast.Name) and left.func.id == fname:
                try:
                    if tuple(_ast.literal_eval(x) for x in left.args) == tuple(args):
                        return _ast.literal_eval(node.comparators[0])
                except Exception:
                    pass
    return None
'''
