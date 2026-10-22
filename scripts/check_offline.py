"""Check that the demo works without internet.

Run with SAMPLE_MODE=true, ideally inside Docker with no network:

    docker build -t trial-autopsy .
    docker run --rm --network none -e SAMPLE_MODE=true trial-autopsy python scripts/check_offline.py

It loads the page and every static asset, then every recorded analysis with its target
protein profile and 3D structure, exactly as the browser would. Exit code 1 on any failure.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SAMPLE_MODE", "true")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{(' · ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


page = client.get("/")
check("index page", page.status_code == 200)
html = page.text
external = re.findall(r'<(?:script|link)[^>]+(?:src|href)="(https?://[^"]+)"', html)
check("no external scripts, styles or fonts", not external, ", ".join(external))
assets = re.findall(r'(?:src|href)="(/static/[^"?]+)', html)
css = client.get("/static/vendor/fonts/fonts.css").text
assets += [f"/static/vendor/fonts/{f}" for f in re.findall(r"url\(([^)]+\.woff2)\)", css)]
for path in assets:
    r = client.get(path)
    check(f"asset {path}", r.status_code == 200 and len(r.content) > 0, f"{len(r.content)} bytes")

for endpoint in ("/api/graph", "/api/accuracy", "/api/calibration", "/api/predictions", "/api/models", "/api/trial-ids", "/api/examples", "/api/settings"):
    check(endpoint, client.get(endpoint).status_code == 200)

analyses = client.get("/api/analyses").json()
runs = [a.get("run_id") or a.get("id") for a in (analyses.get("analyses") if isinstance(analyses, dict) else analyses)]
check("analyses library", len(runs) > 0, f"{len(runs)} analyses")

for run_id in runs:
    r = client.get(f"/api/runs/{run_id}")
    res = (r.json() or {}).get("result") or {}
    check(f"run {run_id}", r.status_code == 200 and res.get("status") == "success")
    trace = client.get(f"/api/runs/{run_id}/trace").json()
    check(f"  trace", len(trace) > 0, f"{len(trace)} events")
    tl = client.get(f"/api/runs/{run_id}/timeline")
    check(f"  timeline", tl.status_code == 200, f"{len(tl.json().get('seen', []))} seen, {len(tl.json().get('hidden', []))} hidden")
    drug = res.get("drug") or {}
    target = (drug.get("targets") or [None])[0]
    if not target:
        print(f"     {run_id}: no protein target (panel hidden by design)")
        continue
    drugs = ",".join([drug.get("name") or ""] + [a for a in (drug.get("aliases") or []) if len(a) > 5][:2])
    p = client.get("/api/target", params={"symbol": target["symbol"], "ensembl": target["id"], "drugs": drugs}).json()
    check(f"  target {target['symbol']} profile", bool(p.get("available")), p.get("reason", ""))
    if p.get("source"):
        params = {"source": p["source"], "id": p["id"], **({"url": p["url"]} if p.get("url") else {})}
        s = client.get("/api/structure", params=params)
        check(f"  structure {p['source']} {p['id']}", s.status_code == 200 and len(s.content) > 1000,
              f"{len(s.content)} bytes")

print(f"\n{'All offline checks passed' if not failures else f'{len(failures)} offline checks failed'}.")
sys.exit(1 if failures else 0)
