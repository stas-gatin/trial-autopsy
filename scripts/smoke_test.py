"""Check every layer the app runs on, in the format of the hackathon's smoke test: one PASS or FAIL line each.

    .venv/bin/python scripts/smoke_test.py

Data: Zetaris answers SQL over its REST API. Build: the Meterless World Model engine is running.
Power: an NVIDIA Nemotron model answers through Meterless Nightshift, and NVIDIA's API answers directly (used by
the extraction steps, which run with reasoning off). Settings come from .env, the same as the app's.
Exit code 1 if any check fails.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import settings  # noqa: E402

NEMOTRON_DIRECT = "nvidia/nemotron-3.5-super-vl-120b-a12b"


def nightshift() -> dict:
    result = settings.test_provider("meterless", "meterless/fast")
    if result["ok"] and "nemotron" not in result["message"].lower():
        return {"ok": False, "message": f"answered, but not by a Nemotron model: {result['message']}"}
    return result


CHECKS = [
    ("Data: Zetaris runs SQL over its REST API", lambda: settings.test_source("zetaris")),
    ("Build: Meterless World Model engine is running", lambda: settings.test_source("meterless")),
    ("Power: Nemotron replies through Meterless Nightshift", nightshift),
    ("Power: Nemotron replies through NVIDIA's API", lambda: settings.test_provider("nvidia", NEMOTRON_DIRECT)),
]


def main() -> int:
    failed = 0
    for name, check in CHECKS:
        try:
            result = check()
        except Exception as exc:   # a check must never stop the others
            result = {"ok": False, "message": f"{type(exc).__name__}: {str(exc)[:200]}"}
        failed += not result["ok"]
        print(f"{'PASS' if result['ok'] else 'FAIL'} {name} ({result['message']})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
