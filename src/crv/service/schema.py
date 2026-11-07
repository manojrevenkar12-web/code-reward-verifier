"""Request bodies to domain objects, with strict validation (the API is the trainer's front door)."""

from typing import Any

from crv.domain.task import AssertList, CheckFunction, Submission, Task, TestSuite, UnittestModule

MAX_SOURCE = 1_000_000


class BadRequest(ValueError):
    pass


def _str(d: dict[str, Any], key: str, default: str | None = None) -> str:
    v = d.get(key, default)
    if not isinstance(v, str):
        raise BadRequest(f"{key} must be a string")
    if len(v) > MAX_SOURCE:
        raise BadRequest(f"{key} is longer than {MAX_SOURCE} characters")
    return v


def tests_from(d: Any) -> TestSuite:
    if not isinstance(d, dict):
        raise BadRequest("tests must be an object")
    kind = d.get("kind")
    if kind == "check_function":
        return CheckFunction(_str(d, "source"), _str(d, "entry_point"))
    if kind == "assert_list":
        asserts = d.get("asserts")
        if not isinstance(asserts, list) or not asserts or not all(isinstance(a, str) for a in asserts):
            raise BadRequest("asserts must be a non-empty list of strings")
        return AssertList(tuple(asserts), _str(d, "setup", ""))
    if kind == "unittest":
        return UnittestModule(_str(d, "source"))
    raise BadRequest("tests.kind must be check_function, assert_list or unittest")


def submission_from(d: Any) -> Submission:
    if not isinstance(d, dict):
        raise BadRequest("each item must be an object")
    task = d.get("task")
    if not isinstance(task, dict):
        raise BadRequest("item.task must be an object")
    t = Task(_str(task, "id", "task"), _str(task, "prompt", ""), tests_from(task.get("tests")))
    return Submission(t, _str(d, "completion"))


def submission_to(sub: Submission) -> dict[str, Any]:
    tests = sub.task.tests
    if isinstance(tests, CheckFunction):
        td: dict[str, Any] = {"kind": "check_function", "source": tests.source, "entry_point": tests.entry_point}
    elif isinstance(tests, AssertList):
        td = {"kind": "assert_list", "asserts": list(tests.asserts), "setup": tests.setup}
    else:
        td = {"kind": "unittest", "source": tests.source}
    return {"task": {"id": sub.task.id, "prompt": sub.task.prompt, "tests": td}, "completion": sub.completion}
