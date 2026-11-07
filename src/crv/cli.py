"""crv serve | crv verify --task task.json --completion solution.py [--lane eval]"""

import argparse
import json
import sys

from crv.domain.policy import load_policies
from crv.service.schema import submission_from


def main() -> None:
    ap = argparse.ArgumentParser(prog="crv")
    sub = ap.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    one = sub.add_parser("verify")
    one.add_argument("--task", required=True, help="task JSON as accepted by POST /v1/verify")
    one.add_argument("--completion", required=True)
    one.add_argument("--lane", default="eval")
    a = ap.parse_args()
    if a.cmd == "serve":
        import uvicorn

        from crv.service.http import create_app

        uvicorn.run(create_app(), host=a.host, port=a.port, log_level="warning")
        return
    from crv.sandbox.zygote import ZygoteClient
    from crv.verify.verifier import Verifier

    with open(a.task) as f, open(a.completion) as g:
        s = submission_from({"task": json.load(f), "completion": g.read()})
    z = ZygoteClient.start(slot=99)
    try:
        v = Verifier(z).verify(s, load_policies()[a.lane])
    finally:
        z.close()
    json.dump(v.to_dict(), sys.stdout, indent=2)
    print()
    sys.exit(0 if v.outcome.value == "passed" else 1)
