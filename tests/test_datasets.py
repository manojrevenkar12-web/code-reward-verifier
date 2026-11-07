from crv.datasets import humaneval, mbpp, mbpp_function_name
from crv.domain.task import AssertList, CheckFunction


def test_both_datasets_load_completely() -> None:
    he, mb = humaneval(), mbpp()
    assert len(he) == 164 and len(mb) == 974
    assert all(isinstance(e.task.tests, CheckFunction) for e in he)
    assert all(isinstance(e.task.tests, AssertList) and e.task.tests.asserts for e in mb)


def test_mbpp_function_names_come_from_the_asserts() -> None:
    first = mbpp()[0]
    assert mbpp_function_name(first.task) == "min_cost"
    names = {mbpp_function_name(e.task) for e in mbpp()}
    assert len(names) > 900
