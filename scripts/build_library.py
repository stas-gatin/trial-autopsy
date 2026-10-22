"""Copy finished analyses (demo examples and historical backtests) into data/library/,
which is committed, so the home page lists them on a fresh clone and in Docker.
Each run's agent trace is copied to data/library/traces/ so the report can show how the
agents worked (critic rounds, agent graph replay) offline.

Usage: python scripts/build_library.py
"""
import json
import pathlib
import shutil

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNS, LIBRARY = ROOT / "data" / "runs", ROOT / "data" / "library"
LOGS, TRACES = ROOT / "logs", ROOT / "data" / "library" / "traces"


def main():
    TRACES.mkdir(parents=True, exist_ok=True)
    copied = traces = 0
    for path in sorted(RUNS.glob("*.json")):
        if not (path.stem.startswith("example-") or path.stem.startswith("bt-")):
            continue
        if json.loads(path.read_text()).get("status") == "success":
            shutil.copy2(path, LIBRARY / path.name)
            copied += 1
            trace = LOGS / f"trace-{path.stem}.jsonl"
            if trace.exists():
                shutil.copy2(trace, TRACES / trace.name)
                traces += 1
    print(f"{copied} analyses and {traces} traces in data/library/")


if __name__ == "__main__":
    main()
