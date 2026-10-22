"""SEC EDGAR: company filings (8-K for US companies, 6-K for foreign issuers) that announce trial results.

Uses EDGAR full-text search (no key). SEC's fair-access policy requires a User-Agent with a contact
e-mail, which http.py sends on every request (Settings -> Contact e-mail).
"""
from __future__ import annotations

import html
import re
from datetime import date

from app.tools import http
from app.tools.documents import Document

SEARCH = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{adsh}/{file}"
RESULT_WORDS = re.compile(r"primary endpoint|topline|top-line|did not meet|met (its|the) primary|results? (from|of) the|"
                          r"futility|discontinu|stopped", re.I)


def search(names: list[str], start: date, end: date, tracer=None) -> list[dict]:
    query = " OR ".join(f'"{n}"' for n in names if n)
    body = http.get_json(SEARCH, params={"q": query, "forms": "8-K,6-K", "dateRange": "custom",
                                         "startdt": start.isoformat(), "enddt": end.isoformat()},
                         tracer=tracer, source="SEC EDGAR")
    hits = []
    for h in (body.get("hits") or {}).get("hits", []):
        src = h.get("_source") or {}
        adsh, _, filename = h.get("_id", "").partition(":")
        ciks = src.get("ciks") or []
        if not (adsh and filename and ciks):
            continue
        hits.append({"adsh": adsh, "file": filename, "cik": str(int(ciks[0])), "form": src.get("form"),
                     "date": src.get("file_date"), "company": (src.get("display_names") or [""])[0],
                     "type": src.get("file_type") or "", "description": src.get("file_description") or ""})
    # Press-release exhibits first (EX-99.x), then the main 6-K, newest first.
    hits.sort(key=lambda x: (not x["type"].startswith("EX-99"), x["form"] != "6-K", -int((x["date"] or "0").replace("-", "") or 0)))
    return hits


def fetch(hit: dict, tracer=None) -> Document:
    url = ARCHIVE.format(cik=hit["cik"], adsh=hit["adsh"].replace("-", ""), file=hit["file"])
    raw = http.get_text(url, tracer=tracer, source="SEC EDGAR")
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
    company = re.sub(r"\s*\(CIK.*", "", hit["company"]).strip()
    return Document(source_id=f"sec:{hit['adsh']}:{hit['file']}", type="press_release",
                    title=f"{company} Form {hit['form']} {hit['type']} ({hit['date']})", text=text[:30000], url=url,
                    pub_date=hit["date"], access="sec",
                    meta={"company": company, "form": hit["form"], "filing": hit["adsh"]})


def announces_result(doc: Document, names: list[str]) -> bool:
    low = doc.text.lower()
    return any(n and n.lower() in low for n in names) and bool(RESULT_WORDS.search(doc.text))
