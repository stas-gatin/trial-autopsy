"""Frozen time-machine archives: data/archive/<case_id>/manifest.json + documents.

An archive holds only documents published before the case's `as_of` date,
collected by a fixed rule (scripts/build_archive.py). In backtest mode the agent
searches these documents instead of the internet.
"""
from __future__ import annotations

import json
import re
from datetime import date

from app import config
from app.tools.documents import Document, visible_on


def exists(case_id: str) -> bool:
    return (config.ARCHIVE / case_id / "manifest.json").exists()


def manifest(case_id: str) -> dict:
    return json.loads((config.ARCHIVE / case_id / "manifest.json").read_text())


def load(case_id: str) -> tuple[date, list[Document]]:
    m = manifest(case_id)
    as_of = date.fromisoformat(m["as_of"])
    docs = []
    for entry in m["documents"]:
        text = (config.ARCHIVE / case_id / entry["file"]).read_text()
        doc = Document(source_id=entry["source_id"], type=entry["type"], title=entry["title"], text=text,
                       url=entry.get("url", ""), pub_date=entry.get("pub_date"), access=entry.get("access", ""),
                       meta=entry.get("meta", {}))
        if visible_on(doc, as_of) or doc.type == "registry":
            docs.append(doc)
    return as_of, docs


def search(docs: list[Document], terms: list[str], limit: int = 8) -> list[Document]:
    """Rank archive documents by term hits (simple, transparent)."""
    scored = []
    for doc in docs:
        text = (doc.title + " " + doc.text).lower()
        score = sum(len(re.findall(re.escape(t.lower()), text)) for t in terms)
        if score:
            scored.append((score, doc))
    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored[:limit]]
