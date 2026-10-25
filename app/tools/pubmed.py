"""PubMed (NCBI E-utilities): the fallback literature source when Europe PMC is unavailable.

It returns records in the same shape as app.tools.europepmc.search, applies the same publication-date cut-off, and
reads open full texts from PubMed Central. Europe PMC query syntax is translated: TITLE:x -> x[ti], ABSTRACT:x ->
x[tiab] (PubMed has no abstract-only field), PUB_TYPE:"y" -> "y"[pt], EXT_ID:n -> n[pmid], PMCID:PMCn -> PMCn[pmc].
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date

import json

from app.tools import http

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = {"tool": "trial-autopsy"}   # NCBI asks for a tool name; no personal data is sent
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"], 1)}


def to_pubmed_query(query: str) -> str:
    """Europe PMC syntax to PubMed syntax, for the forms this app writes."""
    q = query
    q = re.sub(r'\bTITLE:("[^"]+"|[^\s()]+)', lambda m: f"{m.group(1)}[ti]", q)
    q = re.sub(r'\bABSTRACT:("[^"]+"|[^\s()]+)', lambda m: f"{m.group(1)}[tiab]", q)
    q = re.sub(r'\bPUB_TYPE:("[^"]+"|[^\s()]+)', lambda m: f"{m.group(1)}[pt]", q)
    q = re.sub(r"\bEXT_ID:(\d+)", r"\1[pmid]", q)
    q = re.sub(r"\bPMCID:(PMC\d+)", r"\1[pmc]", q)
    return q


def _date(text: str | None) -> str | None:
    """'2016 Apr 2' -> 2016-04-02; '2016 Apr' -> 2016-04-01; '2016' -> 2016-01-01 (month-only dates read as the 1st,
    as Europe PMC stores them)."""
    if not text:
        return None
    m = re.match(r"(\d{4})(?:[ /-]([A-Za-z]{3}|\d{1,2}))?(?:[ /-](\d{1,2}))?", text.strip())
    if not m:
        return None
    year, month, day = int(m.group(1)), m.group(2), m.group(3)
    month_n = _MONTHS.get(month.lower()[:3], None) if month and month.isalpha() else (int(month) if month else 1)
    return f"{year:04d}-{(month_n or 1):02d}-{int(day) if day else 1:02d}"


def search(query: str, as_of: date | None = None, page_size: int = 15, tracer=None) -> list[dict]:
    params = {"db": "pubmed", "term": to_pubmed_query(query), "retmax": str(page_size), "retmode": "json",
              "sort": "relevance", **TOOL}
    if as_of:
        params.update({"datetype": "pdat", "mindate": "1900/01/01", "maxdate": as_of.strftime("%Y/%m/%d")})
    # read as text, so the request goes straight to NCBI: the Zetaris JSON layer does not keep PubMed's field tags
    found = json.loads(http.get_text(f"{EUTILS}/esearch.fcgi", params=params, tracer=tracer, source="PubMed") or "{}")
    ids = (found.get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return []
    summary = json.loads(http.get_text(f"{EUTILS}/esummary.fcgi", params={"db": "pubmed", "id": ",".join(ids),
                                                                           "retmode": "json", **TOOL},
                                       tracer=tracer, source="PubMed") or "{}").get("result") or {}
    abstracts = _abstracts(ids, tracer)
    out = []
    for pmid in ids:
        s = summary.get(pmid) or {}
        if not s:
            continue
        article_ids = {a.get("idtype"): a.get("value") for a in s.get("articleids") or []}
        dates = [d for d in (_date(s.get("epubdate")), _date(s.get("pubdate"))) if d]
        first = min(dates) if dates else None
        if as_of and first and first > as_of.isoformat():
            continue
        pmcid = article_ids.get("pmc")
        out.append({
            "pmid": pmid, "pmcid": pmcid, "doi": article_ids.get("doi"), "title": (s.get("title") or "").rstrip("."),
            "journal": s.get("fulljournalname"), "first_pub_date": first, "is_open_access": bool(pmcid),
            "in_pmc": bool(pmcid), "abstract": abstracts.get(pmid, ""), "pub_type": s.get("pubtype") or [],
            "source": "PubMed",
        })
    return out


def _abstracts(ids: list[str], tracer=None) -> dict[str, str]:
    xml = http.get_text(f"{EUTILS}/efetch.fcgi", params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml",
                                                          **TOOL}, tracer=tracer, source="PubMed")
    out = {}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for art in root.iter("PubmedArticle"):
        pmid = art.findtext(".//PMID")
        parts = []
        for a in art.iter("AbstractText"):
            text = " ".join("".join(a.itertext()).split())
            label = a.get("Label")
            parts.append(f"{label}: {text}" if label and text else text)
        if pmid:
            out[pmid] = " ".join(p for p in parts if p)
    return out


def full_text(pmcid: str, tracer=None) -> str | None:
    """The article body from PubMed Central, or None when only the front matter is available (not open access)."""
    from app.tools.europepmc import xml_to_text
    xml = http.get_text(f"{EUTILS}/efetch.fcgi", params={"db": "pmc", "id": pmcid, "retmode": "xml", **TOOL},
                        tracer=tracer, source="PubMed Central")
    if "<body" not in (xml or ""):
        return None
    return xml_to_text(xml)
