"""Replay every recorded analysis offline and check it reproduces the live run.

    python scripts/check_replay.py

Each analysis in data/library/ is run again in SAMPLE_MODE: model answers come from data/llm_cache/, source
responses from the offline cache, and every check (quotes, calculator, Verifier, critic) runs again. A replay is
saved apart from the live runs (data/runs/replay/, logs/replay/), so it never overwrites a live result or log.
Each line compares the outcome, the expected hazard ratio, the power and the number of facts. Exit code 1 if any
replay differs.
"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SAMPLE_MODE"] = "true"
os.environ.setdefault("METERLESS_AUTOSTART", "false")

from app.main import RunRequest, _execute  # noqa: E402


def summary(run: dict) -> tuple:
    out = run.get("output") or {}
    calc, verdict = out.get("calculator") or {}, out.get("verdict") or {}
    return run.get("status"), verdict.get("outcome"), calc.get("expected_hr"), calc.get("power"), len(out.get("facts") or [])


def main() -> int:
    requests = sorted((ROOT / "eval" / "backtests").glob("*.json")) + sorted((ROOT / "input_examples").glob("*.json"))
    checked, differ = set(), 0
    for path in requests:
        body = json.loads(path.read_text())
        run_id = body["run_id"]
        live_path = ROOT / "data" / "library" / f"{run_id}.json"
        if run_id in checked or not live_path.exists():
            continue
        checked.add(run_id)
        live, replay = summary(json.loads(live_path.read_text())), summary(_execute(RunRequest(**body), run_id))
        same = live == replay
        differ += not same
        print(f"{'SAME' if same else 'DIFF'} {run_id}: live {live} | replay {replay}", flush=True)
    print(f"{len(checked)} analyses replayed offline, {differ} differ from the live run")
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
