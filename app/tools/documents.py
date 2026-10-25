"""Document model shared by all tools, and the quote checker."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date


@dataclass
class Document:
    source_id: str            # stable id, e.g. "ctgov:NCT01687998", "pmid:28514624"
    type: str                 # registry | journal | press_release | sec_filing | database | user_input
    title: str
    text: str
    url: str = ""
    pub_date: str | None = None   # ISO date of first public availability
    access: str = ""          # open_full_text | abstract_only | registry | press_release | sec | database | user
    meta: dict = field(default_factory=dict)

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("text")
        d["chars"] = len(self.text)
        return d


def visible_on(doc: Document, as_of: date | None) -> bool:
    """Time machine: a document is visible only if published on or before as_of."""
    if as_of is None or not doc.pub_date:
        return as_of is None or doc.type in ("user_input",)
    return doc.pub_date[:10] <= as_of.isoformat()


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("−", "-").replace("–", "-").replace("—", "-")
    text = re.sub(r"[‘’“”]", "'", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def verify_quote(document_text: str, quote: str) -> bool:
    """True if the quote occurs verbatim (modulo whitespace, case, dash and quote styles)."""
    if not quote or len(quote) < 8:
        return False
    return _normalize(quote) in _normalize(document_text)


def chunks(text: str, size: int = 12000, overlap: int = 600) -> list[str]:
    if len(text) <= size:
        return [text]
    out, start = [], 0
    while start < len(text):
        out.append(text[start:start + size])
        start += size - overlap
    return out
