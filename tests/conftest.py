import os
from collections.abc import Iterator

import pytest

from crv.sandbox.zygote import ZygoteClient


def _can_isolate() -> bool:
    return os.name == "posix" and os.geteuid() == 0


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _can_isolate():
        return
    skip = pytest.mark.skip(reason="sandbox tests need root on Linux (run with sudo)")
    for item in items:
        if "sandbox" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def zygote() -> Iterator[ZygoteClient]:
    z = ZygoteClient.start(slot=7)
    yield z
    z.close()
