"""Build a frozen time-machine archive for a golden-set case.

Rule (fixed, applied identically to every case):
  as_of = the case's readout_date (the day of the first public topline announcement)
  1. ClinicalTrials.gov record in "design-only" view (status, stop reason, results removed)
  2. Europe PMC documents published BEFORE as_of that mention the drug or its code names
     (same queries as live mode); ranked: drug in title, then trials, then cardiometabolic topic,
     then most recent; at most 10; open full text when available (rule v2). A paper found only by
     the trial acronym must spell it in capitals and be on a cardiometabolic topic, so common-word
     acronyms (SPRINT, STRENGTH) do not pull in papers about running or muscle strength (rule v4)
  3. Up to 3 genetic-association papers per target published BEFORE as_of (rule v3), and up to 2 design or baseline
     papers of the outcome trial itself (title says design, rationale, baseline characteristics or protocol, and
     spells the acronym), published BEFORE as_of: they state the planned events and the population (rule v5)
     In 2 and 3, a paper dated only to a month (stored as the 1st) in the readout month is left out: it may
     have been printed after the readout (rule v4)
  4. The topline announcement itself (press release on the readout day): the Wayback Machine capture nearest that day
     (within a year, same page), else the live page if it was not redirected elsewhere (rule v5)
Nothing is added by hand. Documents that reveal the outcome cannot exist before as_of,
except the topline announcement, which is what a real analyst would have on that day.

Usage: python scripts/build_archive.py accelerate [--max-docs 10]
"""
import argparse
import html
import json
import pathlib
import re
import sys
from datetime import date, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.agents import case_builder  # noqa: E402
from app.agents.intake import PLACEBO  # noqa: E402
from app.tools import ctgov, europepmc, http, opentargets  # noqa: E402

WAYBACK = "https://web.archive.org/web/{date}/{url}"
TOPIC = re.compile(case_builder.CARDIO.pattern + r"|hypertens|blood pressure|triglycer|renal|kidney|diabet", re.I)


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def _get(url: str):
    """(text, final URL) of a page, or (None, None). Redirects are followed so that they can be checked."""
    import httpx
    try:
        with httpx.Client(timeout=60, headers=http._headers(), follow_redirects=True) as client:
            r = client.get(url)
    except httpx.HTTPError:
        return None, None
    return (r.text, str(r.url)) if r.status_code == 200 else (None, None)


def _page(url: str) -> str:
    """Host and path, without scheme, 'www.' or a trailing slash, to tell a moved page from the page asked for."""
    from urllib.parse import urlparse
    u = urlparse(url)
    return (u.netloc.lower().removeprefix("www.") + u.path.rstrip("/")).lower() + ("?" + u.query if u.query else "")


def _drop_later_text(text: str, on: str) -> str:
    """Leave out every sentence that names a year more than one year after the readout: it belongs to the site the
    page is shown on today (a news sidebar, a copyright line), not to the announcement."""
    limit = int(on[:4]) + 1
    parts = re.split(r"(?<=[.!?])\s+|\s+\u00b7\s+", text)
    keep = [x for x in parts if not any(int(y) > limit for y in re.findall(r"\b(19[5-9]\d|20\d\d)\b", x))]
    return " ".join(keep)


def fetch_announcement(url: str, on: str) -> tuple[str | None, str]:
    """The announcement as it was published (rule v5). First the Wayback Machine capture nearest the readout day, raw
    (no Wayback toolbar), if it was taken within a year of it and is still the same page; only then the live page,
    and only if it was not redirected elsewhere: a live page carries today's sidebars and footers, and an old address
    may now redirect to unrelated news. Returns (text, provenance)."""
    from datetime import date as _date
    day = on.replace("-", "")
    text, final = _get(f"https://web.archive.org/web/{day}id_/{url}")
    if text and final:
        m = re.search(r"/web/(\d{8})\d*(?:id_)?/(.+)$", final)
        if m:
            taken = _date(int(m.group(1)[:4]), int(m.group(1)[4:6]), int(m.group(1)[6:8]))
            gap = (taken - _date.fromisoformat(on)).days
            if -30 <= gap <= 365 and _page(m.group(2)) == _page(url):
                body = _drop_later_text(html_to_text(text), on)
                if len(body) > 400:
                    return body[:20000], f"Wayback Machine capture of {taken.isoformat()}"
    text, final = _get(url)
    if text and final and _page(final) == _page(url):
        body = _drop_later_text(html_to_text(text), on)
        if len(body) > 400:
            return body[:20000], "live page (no usable Wayback capture), sentences naming later years left out"
    return None, "not readable"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id")
    parser.add_argument("--max-docs", type=int, default=10)
    args = parser.parse_args()

    gold = json.loads((config.DATA / "golden_set" / f"{args.case_id}.json").read_text())
    meta = gold["meta"]
    as_of = date.fromisoformat(meta["readout_date"])
    before = as_of - timedelta(days=1)
    out = config.ARCHIVE / args.case_id
    (out / "docs").mkdir(parents=True, exist_ok=True)
    entries = []

    def save(doc, note=""):
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", doc.source_id)[:80] + ".txt"
        (out / "docs" / name).write_text(doc.text)
        entries.append({"source_id": doc.source_id, "file": f"docs/{name}", "type": doc.type, "title": doc.title,
                        "url": doc.url, "pub_date": doc.pub_date, "access": doc.access,
                        "meta": {k: v for k, v in doc.meta.items() if k != "summary"} | (
                            {"summary": doc.meta["summary"]} if "summary" in doc.meta else {}),
                        "note": note})

    study = ctgov.get_study(meta["nct_id"])
    save(ctgov.to_document(study, as_of), "design-only registry view")

    summary = ctgov.summarize(study)
    names = [meta["drug"].split(" (")[0]]
    for i in summary["interventions"]:
        if i["type"] in ("DRUG", "BIOLOGICAL") and not PLACEBO.search(i["name"]):
            names.append(i["name"])
    drug = None
    for n in names:
        hit = opentargets.find_drug(re.sub(r"\b\d+(\.\d+)?\s?(mg|g)\b.*", "", n).strip())
        if hit:
            drug = opentargets.drug_profile(hit["id"])
            break
    aliases = [drug["name"].lower()] + [x["label"] for x in (drug.get("synonyms") or [])] if drug else names
    record_stub = {"title": "", "abstract": ""}
    tokens = sorted({t for n in aliases for t in re.split(r"[^A-Za-z0-9]+", n)
                     if (len(t) >= 5 or any(ch.isdigit() for ch in t))
                     and t.lower() not in case_builder.generic_tokens_for(summary.get("sponsor"), aliases)})
    name_clause = " OR ".join(f'TITLE:"{n}" OR ABSTRACT:"{n}"' for n in aliases[:6])
    queries = [f"({name_clause})", f"({' OR '.join(tokens[:8])}) AND (PUB_TYPE:\"Clinical Trial, Phase II\" OR "
                                   f"PUB_TYPE:\"Clinical Trial, Phase III\" OR PUB_TYPE:\"Randomized Controlled Trial\")"]
    def dated_before(r):
        d = r.get("first_pub_date") or ""
        return not (d.endswith("-01") and d[:7] == meta["readout_date"][:7])

    acronym = (meta.get("trial_name") or "").split(" (")[0].strip()
    if meta.get("trial_name"):
        queries.append(f'"{meta["trial_name"]}"')
    acronym_re = re.compile(rf"(?<![A-Za-z0-9]){re.escape(acronym)}(?![A-Za-z0-9])") if acronym else None
    candidates = {}
    for q in queries:
        for r in filter(dated_before, europepmc.search(q, as_of=before, page_size=25)):
            if case_builder._mentions(r, aliases):
                candidates.setdefault(r.get("pmid") or r["title"], r)
            elif acronym_re and case_builder._mentions(r, [meta["trial_name"], acronym]):
                text = r["title"] + " " + r["abstract"]
                if acronym_re.search(text) and TOPIC.search(text):
                    candidates.setdefault(r.get("pmid") or r["title"], r)
    chosen = sorted(candidates.values(), key=lambda r: case_builder._rank(r, aliases))[:args.max_docs]
    for record in chosen:
        text = europepmc.full_text(record["pmcid"]) if record.get("pmcid") and record.get("is_open_access") else None
        save(europepmc.to_document(record, text))

    # The outcome trial's own design or baseline paper, published before as_of (rule v5).
    if acronym:
        have = {r.get("pmid") or r["title"] for r in chosen}
        design = [r for r in filter(dated_before, europepmc.search(case_builder.design_query(acronym), as_of=before,
                                                                    page_size=10))
                  if case_builder.is_design_paper(r, acronym) and (r.get("pmid") or r["title"]) not in have][:2]
        for record in design:
            text = europepmc.full_text(record["pmcid"]) if record.get("pmcid") and record.get("is_open_access") else None
            doc = europepmc.to_document(record, text)
            doc.meta["purpose"] = "design"
            save(doc, "design or baseline paper of the outcome trial")

    # Genetic-association papers on the target(s), published before as_of (rule v3).
    targets = [t for m in ((drug or {}).get("mechanismsOfAction") or {}).get("rows", []) for t in m.get("targets") or []]
    for t in {t["approvedSymbol"]: t for t in targets}.values():
        q = (f'(TITLE:"{t["approvedSymbol"]}" OR ABSTRACT:"{t["approvedSymbol"]}") AND {case_builder.GENETIC_TERMS}')
        records = [r for r in europepmc.search(q, as_of=before, page_size=10)
                   if dated_before(r) and t["approvedSymbol"].lower() in (r["title"] + r["abstract"]).lower()][:3]
        for r in records:
            doc = europepmc.to_document(r)
            doc.meta["purpose"] = "genetics"
            save(doc, "genetic association paper on the target")

    announcements = [s for s in gold["sources"] if s["type"] == "press_release" and s.get("pub_date") == meta["readout_date"]]
    for s in announcements:
        text, provenance = fetch_announcement(s["url"], meta["readout_date"])
        print(f"announcement {s['url']}: {provenance}")
        if text:
            from app.tools.documents import Document
            save(Document(source_id=f"announcement:{args.case_id}", type="press_release", title=s["title"], text=text,
                          url=s["url"], pub_date=meta["readout_date"], access="press_release"),
                 f"topline announcement on the readout day ({provenance})")
            break

    manifest = {"case_id": args.case_id, "nct_id": meta["nct_id"], "acronym": meta.get("trial_name"),
                "title": summary.get("title"), "drug": names[0], "as_of": as_of.isoformat(),
                "readout_date": meta["readout_date"], "mode": "autopsy" if announcements else "forecast",
                "rule": __doc__.split("Usage:")[0].strip(), "documents": entries}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"{args.case_id}: {len(entries)} documents as of {as_of}")
    for e in entries:
        print(f"  {e['pub_date']} {e['type']:13} {e['access']:15} {e['title'][:70]}")


if __name__ == "__main__":
    main()
