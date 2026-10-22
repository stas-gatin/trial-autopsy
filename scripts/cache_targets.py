"""Fetch target-protein profiles and 3D structures for every analysis in data/library/ and copy the
responses into data/sample_cache/, so the "Meet the target" panel works offline (SAMPLE_MODE, Docker).

Usage: python scripts/cache_targets.py
"""
import json
import os
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    log = tempfile.NamedTemporaryFile("r", suffix=".log", delete=False).name
    os.environ["HTTP_KEY_LOG"] = log
    from app.tools import structures  # noqa: E402  (after HTTP_KEY_LOG is set)
    seen = set()
    for path in sorted((ROOT / "data" / "library").glob("*.json")):
        run = json.loads(path.read_text())
        drug = run.get("drug") or {}
        for target in (drug.get("targets") or [])[:1]:
            names = [drug.get("name")] + [a for a in drug.get("aliases", []) if len(a) > 5][:2]
            key = (target["id"], tuple(names))
            if key in seen:
                continue
            seen.add(key)
            profile = structures.target_profile(target["symbol"], target["id"], names)
            text = structures.structure_file(profile) if profile.get("source") else None
            print(f"{target['symbol']:8} {profile.get('label')}  ({len(text or '') // 1024} KB)")
    src, dst = ROOT / "data" / "cache" / "http", ROOT / "data" / "sample_cache" / "http"
    keys = {k.strip() for k in open(log) if k.strip()}
    copied = 0
    for k in keys:
        if (src / f"{k}.json").exists():
            shutil.copy2(src / f"{k}.json", dst / f"{k}.json")
            copied += 1
    size = sum(p.stat().st_size for p in dst.glob("*.json"))
    print(f"{copied} responses copied; sample cache now {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
