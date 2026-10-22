"""Re-run every library analysis offline from its recordings (SAMPLE_MODE) and rebuild data/library/.

Model outputs and source responses are replayed, so the verdicts do not change; use it after adding a
new field to the result (for example token usage) or a new trace event.

Usage: python scripts/rerun_library.py
"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SAMPLE_MODE"] = "true"

from app import orchestrator  # noqa: E402
from scripts import build_library  # noqa: E402

RUNS = ROOT / "data" / "runs"


def main():
    requests = [json.loads(p.read_text()) for p in sorted((ROOT / "eval" / "backtests").glob("*.json"))]
    requests += [json.loads(p.read_text()) for p in sorted((ROOT / "input_examples").glob("*.json"))]
    RUNS.mkdir(parents=True, exist_ok=True)
    for req in requests:
        before = RUNS / f"{req['run_id']}.json"
        old = json.loads(before.read_text())["output"]["verdict"] if before.exists() else None
        result = orchestrator.run(req["input"], run_id=req["run_id"])
        before.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        new = (result.get("output") or {}).get("verdict")
        same = "same verdict" if old == new else "VERDICT CHANGED"
        print(f"{req['run_id']:24s} {result['status']:8s} {same}")
    build_library.main()


if __name__ == "__main__":
    main()
