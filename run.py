"""Entry point.

  python run.py                         start the API + web UI on 0.0.0.0:$PORT (default 8000)
  python run.py --input FILE.json       run the agent once on a request file and print the JSON result
"""
import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser(description="Trial Autopsy")
    parser.add_argument("--input", help="request JSON file (same shape as POST /run)")
    parser.add_argument("--sample", action="store_true", help="force SAMPLE_MODE (offline, recorded data)")
    args = parser.parse_args()
    if args.sample:
        os.environ["SAMPLE_MODE"] = "true"

    if args.input:
        from app.main import RunRequest, _execute
        with open(args.input) as f:
            req = RunRequest(**json.load(f))
        result = _execute(req, req.run_id or "cli-run")
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False, default=str)
        print()
        return

    start_meterless()
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))


def start_meterless():
    """Start the local Meterless World Model service (services/meterless) unless it is already running."""
    import atexit
    import pathlib
    import subprocess
    if os.getenv("METERLESS_AUTOSTART", "true").lower() in ("0", "false", "no", "off"):
        return
    service = pathlib.Path(__file__).parent / "services" / "meterless"
    tsx = service / "node_modules" / ".bin" / "tsx"
    if not tsx.exists():
        print("Meterless service not installed: run `npm --prefix services/meterless install` to enable it.", file=sys.stderr)
        return
    from app.engines import meterless
    try:
        meterless.health()
        return
    except ConnectionError:
        pass
    proc = subprocess.Popen([str(tsx), "server.ts"], cwd=service)
    atexit.register(proc.terminate)


if __name__ == "__main__":
    main()
