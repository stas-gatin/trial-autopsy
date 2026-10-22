"""Reference coefficient table (data/reference/coefficients.json)."""
from __future__ import annotations

import functools
import json

from app import config


@functools.lru_cache(maxsize=1)
def table() -> dict:
    return json.loads((config.DATA / "reference" / "coefficients.json").read_text())


def entry(entry_id: str) -> dict:
    for e in table()["entries"]:
        if e["id"] == entry_id:
            return e
    raise KeyError(entry_id)


def sources_for(entry_id: str) -> list[dict]:
    ids = set(entry(entry_id).get("source_ids", []))
    return [s for s in table()["sources"] if s["id"] in ids]


def causal_status(biomarker: str) -> str | None:
    for e in table()["entries"]:
        if e["biomarker"].lower() == biomarker.lower():
            return e["causal"]
    return None


def brief() -> list[dict]:
    """Compact view for prompts: biomarker, causal, effect, scale, source ids."""
    out = []
    for e in table()["entries"]:
        eff = e.get("effect", {})
        out.append({"id": e["id"], "biomarker": e["biomarker"], "causal": e["causal"],
                    "effect": f"{eff.get('measure')} {eff.get('value')} ({eff.get('ci_low')}-{eff.get('ci_high')}) "
                              f"per {eff.get('per_unit')}", "timescale": eff.get("timescale")})
    return out
