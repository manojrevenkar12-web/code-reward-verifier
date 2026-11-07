from collections.abc import Iterator
from typing import Any

import pytest
from starlette.testclient import TestClient

from crv import config
from crv.domain.policy import ResourcePolicy
from crv.service.http import create_app

pytestmark = pytest.mark.sandbox

TASK = {
    "id": "demo",
    "prompt": "def add(a, b):\n",
    "tests": {"kind": "check_function", "source": "def check(c):\n    assert c(2, 3) == 5\n", "entry_point": "add"},
}


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    s: dict[str, Any] = {**config.load("service.json"), "slot_base": 30}
    s["lanes"] = {"rl": {"weight": 3, "capacity": 50}, "eval": {"weight": 1, "capacity": 10}}
    with TestClient(create_app(s)) as c:
        yield c


def test_a_batch_comes_back_in_order(client: TestClient) -> None:
    items = [{"task": TASK, "completion": c} for c in ("    return a + b\n", "    return a * b\n", "    print(True)\n")]
    r = client.post("/v1/verify", json={"lane": "rl", "items": items})
    assert r.status_code == 200
    assert [v["outcome"] for v in r.json()["verdicts"]] == ["passed", "failed", "failed"]
    assert [v["reward"] for v in r.json()["verdicts"]] == [1.0, 0.0, 0.0]


def test_health_reports_isolation(client: TestClient) -> None:
    h = client.get("/healthz").json()
    assert h["status"] == "ok" and h["isolation"] == {"network": True, "uid_drop": True, "private_tmp": True}


def test_bad_requests_are_rejected_before_any_work(client: TestClient) -> None:
    assert (
        client.post("/v1/verify", json={"lane": "training", "items": [{"task": TASK, "completion": ""}]}).status_code
        == 400
    )
    assert (
        client.post("/v1/verify", json={"items": [{"task": {"tests": {"kind": "x"}}, "completion": ""}]}).status_code
        == 400
    )
    assert client.post("/v1/verify", content=b"{not json").status_code == 400


def test_a_full_lane_pushes_back(client: TestClient) -> None:
    items = [{"task": TASK, "completion": "    return a + b\n"}] * 11
    r = client.post("/v1/verify", json={"lane": "eval", "items": items})
    assert r.status_code == 429 and r.headers["Retry-After"] == "1"


def test_a_hung_worker_is_replaced_and_the_verdict_is_an_infra_error() -> None:
    s: dict[str, Any] = {**config.load("service.json"), "slot_base": 34, "workers": 1, "watchdog_grace_s": 0.5}
    quick = ResourcePolicy(wall_s=0.5, cpu_s=0.5, memory_mb=128, max_output_bytes=1000, max_processes=4, max_calls=10)
    hang = {
        **TASK,
        "tests": {
            "kind": "check_function",
            "source": "def check(c):\n    while True:\n        pass\n",
            "entry_point": "add",
        },
    }
    with TestClient(create_app(s, {"rl": quick, "eval": quick})) as c:
        r = c.post("/v1/verify", json={"lane": "rl", "items": [{"task": hang, "completion": "    return 1\n"}]})
        v = r.json()["verdicts"][0]
        assert (v["outcome"], v["reward"]) == ("infra_error", None)
        ok = c.post("/v1/verify", json={"lane": "rl", "items": [{"task": TASK, "completion": "    return a + b\n"}]})
        assert ok.json()["verdicts"][0]["outcome"] == "passed"
        assert "crv_workers_replaced_total 1" in c.get("/metrics").text


def test_metrics_count_outcomes(client: TestClient) -> None:
    client.post("/v1/verify", json={"lane": "eval", "items": [{"task": TASK, "completion": "    return a + b\n"}]})
    text = client.get("/metrics").text
    assert 'crv_verifications_total{lane="eval",outcome="passed"}' in text
