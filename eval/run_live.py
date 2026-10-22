"""Run cases live on the configured models (default: NVIDIA Nemotron 3) and save each result.

    python -m eval.run_live --out data/runs bt:accelerate bt:cantos example:example_1_pelacarsen_autopsy
    python -m eval.run_live --out data/runs --all            # 14 backtests + the demo examples

Backtests replay the frozen archive (documents published before each readout), so only the models are live.
Runs in parallel threads; each result goes to <out>/<run_id>.json.
"""
import argparse
import json
import os
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SAMPLE_MODE"] = "false"

from app import orchestrator  # noqa: E402


def requests(names: list[str], everything: bool) -> list[dict]:
    if everything:
        names = [f"bt:{p.stem}" for p in sorted((ROOT / "eval" / "backtests").glob("*.json"))] + \
                [f"example:{p.stem}" for p in sorted((ROOT / "input_examples").glob("*.json"))]
    out = []
    for n in names:
        kind, _, stem = n.partition(":")
        folder = ROOT / ("eval/backtests" if kind == "bt" else "input_examples")
        out.append(json.loads((folder / f"{stem}.json").read_text()))
    return out


def run_one(req: dict, out: pathlib.Path) -> str:
    started = time.time()
    try:
        result = orchestrator.run(req["input"], run_id=req["run_id"])
    except Exception as exc:  # keep the batch going
        return f"{req['run_id']:28s} CRASH {type(exc).__name__}: {str(exc)[:160]}"
    (out / f"{req['run_id']}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    o = result.get("output") or {}
    c, v = o.get("calculator") or {}, o.get("verdict") or {}
    return (f"{req['run_id']:28s} {result['status']:8s} {time.time() - started:5.0f}s  HR {c.get('expected_hr')}  "
            f"power {c.get('power')}  predicted {c.get('outcome')}  primary {v.get('primary_category')}"
            + (f"  ERROR {result.get('error', {}).get('type')}" if result['status'] != 'success' else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default="data/runs")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    reqs = requests(args.cases, args.all)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for fut in as_completed([pool.submit(run_one, r, out) for r in reqs]):
            print(fut.result(), flush=True)


if __name__ == "__main__":
    main()
