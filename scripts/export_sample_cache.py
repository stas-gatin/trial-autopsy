"""Copy the HTTP cache entries used by the recorded examples into data/sample_cache/ (committed),
so SAMPLE_MODE works on a fresh clone without network or keys.

The examples are replayed offline (SAMPLE_MODE, with the local HTTP cache): nothing is called live, so no
recording is overwritten. Run it after the examples were recorded live.

Usage: python scripts/export_sample_cache.py input_examples/*.json
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "cache" / "http"
DST = ROOT / "data" / "sample_cache" / "http"


def main():
    DST.mkdir(parents=True, exist_ok=True)
    keys = set()
    for request in sys.argv[1:]:
        with tempfile.NamedTemporaryFile("r", suffix=".log", delete=False) as log:
            env = dict(os.environ, HTTP_KEY_LOG=log.name, SAMPLE_MODE="true", METERLESS_AUTOSTART="false")
            subprocess.run([sys.executable, "run.py", "--input", request], cwd=ROOT, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            keys |= {k.strip() for k in open(log.name) if k.strip()}
    copied = 0
    for key in keys:
        src = SRC / f"{key}.json"
        if src.exists():
            shutil.copy2(src, DST / src.name)
            copied += 1
    size = sum(p.stat().st_size for p in DST.glob("*.json"))
    print(f"{copied} cached responses exported ({size / 1e6:.1f} MB) for {len(sys.argv) - 1} requests")


if __name__ == "__main__":
    main()
