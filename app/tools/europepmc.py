"""Europe PMC: literature search with a publication-date cut-off, abstracts and open full texts."""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import date

from app.tools import http
from app.tools.documents import Document

API = "https://www.ebi.ac.uk/europepmc/webservices/rest"


# When Europe PMC is down (HTTP 5xx, 429 or no connection), PubMed answers instead, and Europe PMC is not asked again
# for a few minutes, so an outage costs one set of retries, not one per query.
_OUTAGE = {"until": 0.0, "why": ""}
OUTAGE_PAUSE_S = 600


def is_outage(exc: Exception) -> bool:
    """A service failure (5xx, 429, timeout, no connection), as opposed to an answer such as 404."""
    text = str(exc)
    return not re.search(r"HTTP 4(?!29)\d\d", text)


def _fallback(kind: str, tracer, why: str):
    if tracer:
        tracer.log("Tools", "source_fallback", input_summary=f"Europe PMC {kind}",
                   output_summary=f"Europe PMC unavailable ({why[:120]}); PubMed used instead",
                   status="warning", source="PubMed")


def _down(exc: Exception):
    _OUTAGE.update(until=time.time() + OUTAGE_PAUSE_S, why=str(exc))


def search(query: str, as_of: date | None = None, page_size: int = 15, tracer=None) -> list[dict]:
    from app.tools import pubmed
    if time.time() < _OUTAGE["until"]:
        _fallback("search", tracer, _OUTAGE["why"])
        return pubmed.search(query, as_of, page_size, tracer)
    try:
        return _search(query, as_of, page_size, tracer)
    except http.SourceError as exc:
        if not is_outage(exc):
            raise
        _down(exc)
        _fallback("search", tracer, str(exc))
        return pubmed.search(query, as_of, page_size, tracer)


def _search(query: str, as_of: date | None = None, page_size: int = 15, tracer=None) -> list[dict]:
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
    from app.tools import pubmed
    miss = config.DATA / "cache" / "negative" / f"{pmcid}.miss"
    if miss.exists():
        return None
    if time.time() < _OUTAGE["until"]:
        _fallback("full text", tracer, _OUTAGE["why"])
        return _pmc_full_text(pmcid, tracer)
    try:
        xml = http.get_text(f"{API}/{pmcid}/fullTextXML", tracer=tracer, source="Europe PMC full text")
    except http.OfflineMiss:
        return None
    except http.SourceError as exc:
        if is_outage(exc):     # the service is down: not evidence that the article has no full text
            _down(exc)
            _fallback("full text", tracer, str(exc))
            return _pmc_full_text(pmcid, tracer)
        miss.parent.mkdir(parents=True, exist_ok=True)
        miss.write_text("full text unavailable")
        return None
    return xml_to_text(xml)


def _pmc_full_text(pmcid: str, tracer=None) -> str | None:
    from app.tools import pubmed
    try:
        return pubmed.full_text(pmcid, tracer)
    except (http.SourceError, http.OfflineMiss):
        return None


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
        url=(f"https://pubmed.ncbi.nlm.nih.gov/{record['pmid']}/" if record.get("source") == "PubMed" else
             f"https://europepmc.org/article/MED/{record['pmid']}") if record.get("pmid") else "",
        pub_date=record.get("first_pub_date"), access="open_full_text" if full else "abstract_only",
        meta={k: record.get(k) for k in ("pmid", "pmcid", "doi", "journal")},
    )
