"""Europe PMC: literature search with a publication-date cut-off, abstracts and open full texts."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date

from app.tools import http
from app.tools.documents import Document

API = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def search(query: str, as_of: date | None = None, page_size: int = 15, tracer=None) -> list[dict]:
    q = query
    if as_of:
        q = f"({query}) AND FIRST_PDATE:[1900-01-01 TO {as_of.isoformat()}]"
    from app.tools import zetaris
    body = http.get_json(f"{API}/search", params={"query": q, "format": "json", "resultType": "core",
                                                 "pageSize": str(page_size), "sort": "CITED desc"},
                         tracer=tracer, source="Europe PMC",
                         zetaris_read=lambda url, params: zetaris.europepmc_search(url, params, as_of))
    out = []
    for r in body.get("resultList", {}).get("result", []):
        out.append({
            "pmid": r.get("pmid"), "pmcid": r.get("pmcid"), "doi": r.get("doi"), "title": r.get("title", ""),
            "journal": (r.get("journalInfo") or {}).get("journal", {}).get("title"),
            "first_pub_date": r.get("firstPublicationDate"), "is_open_access": r.get("isOpenAccess") == "Y",
            "in_pmc": r.get("inPMC") == "Y", "abstract": _clean(r.get("abstractText", "")),
            "pub_type": ((r.get("pubTypeList") or {}).get("pubType") or []),
        })
    return out


def full_text(pmcid: str, tracer=None) -> str | None:
    """Open full text, or None. Failures are remembered so document selection stays reproducible."""
    from app import config
    miss = config.DATA / "cache" / "negative" / f"{pmcid}.miss"
    if miss.exists():
        return None
    try:
        xml = http.get_text(f"{API}/{pmcid}/fullTextXML", tracer=tracer, source="Europe PMC full text")
    except http.OfflineMiss:
        return None
    except http.SourceError:
        miss.parent.mkdir(parents=True, exist_ok=True)
        miss.write_text("full text unavailable")
        return None
    return xml_to_text(xml)


def xml_to_text(xml: str) -> str:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return _clean(xml)
    parts = []
    for elem in root.iter():
        tag = elem.tag.split("}")[-1]
        if tag in ("title", "p", "caption", "td", "th", "label"):
            text = " ".join("".join(elem.itertext()).split())
            if text:
                parts.append(text + (" |" if tag in ("td", "th") else ""))
        if tag == "tr":
            parts.append("\n")
    text = " ".join(parts)
    return re.sub(r" \n ", "\n", text)


def _clean(text: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", text or "").split())


def matches_record(record: dict, text: str) -> bool:
    """Guard against mislinked full texts: the title's content words must appear near the top."""
    words = [w for w in re.findall(r"[a-z]{5,}", record["title"].lower())][:8]
    if not words:
        return True
    head = text[:4000].lower()
    return sum(w in head for w in words) >= max(2, len(words) // 2)


def to_document(record: dict, text: str | None = None) -> Document:
    if text and not matches_record(record, text):
        text = None  # full text belongs to another article; fall back to the abstract
    full = bool(text)
    body = text if full else f"{record['title']}\n\n{record['abstract']}"
    sid = f"pmcid:{record['pmcid']}" if full and record.get("pmcid") else f"pmid:{record.get('pmid') or record.get('doi')}"
    return Document(
        source_id=sid, type="journal", title=record["title"], text=body,
        url=f"https://europepmc.org/article/MED/{record['pmid']}" if record.get("pmid") else "",
        pub_date=record.get("first_pub_date"), access="open_full_text" if full else "abstract_only",
        meta={k: record.get(k) for k in ("pmid", "pmcid", "doi", "journal")},
    )
