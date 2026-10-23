"""Case Builder agent: gathers evidence and extracts quoted facts into the World Model.

Steps: target genetics (Open Targets) -> literature (Europe PMC, or the frozen
archive in backtest mode) -> per-document extraction by the model -> code checks
every quote against the document -> verified facts are ingested with provenance.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

from app.agents import prompts
from app.agents.state import RunState
from app.llm import generator
from app.tools import archive, europepmc, opentargets
from app.tools.documents import Document, _normalize, verify_quote

AGENT = "CaseBuilder"
MAX_LITERATURE_DOCS = 6
EXCERPT_CHARS = 14000
KEY_TERMS = re.compile(
    r"\b(LDL|apoB|apolipoprotein B|non-HDL|HDL|Lp\(a\)|lipoprotein\(a\)|triglycer|CRP|interleukin|IL-6|Lp-PLA2|"
    r"phospholipase|blood pressure|hazard ratio|HR|95% CI|confidence interval|events?|randomi[sz]ed|follow-up|"
    r"power|placebo|primary end ?point|primary outcome|enrolled|median)\b", re.I)
NUMBER = re.compile(r"\d")
GENERIC_TOKENS = {"sodium", "calcium", "acid", "hydrochloride", "tablet", "injection", "placebo", "ionis", "free"}


def _skip(state: RunState, source: str, what: str) -> bool:
    from app import config
    if config.source_enabled(source):
        return False
    state.tracer.log(AGENT, "source_disabled", output_summary=f"{what} skipped: {config.SOURCES[source]['label']} "
                     f"is switched off in Settings", status="warning")
    state.warnings.append(f"{config.SOURCES[source]['label']} disabled in Settings: {what} skipped")
    return True


def gather_genetics(state: RunState):
    if _skip(state, "opentargets", "target genetics"):
        return
    for target in state.drug.get("targets", []):
        try:
            g = opentargets.target_genetics(target["id"], tracer=state.tracer)
        except Exception as exc:
            state.warnings.append(f"genetics lookup failed for {target['symbol']}: {exc}")
            continue
        cutoff = state.as_of.isoformat() if state.as_of else None
        for ev in g["top_evidence"]:
            ev["published_before_cutoff"] = (cutoff is None or (ev.get("publication_date") or "9999") <= cutoff)
        g["evidence_before_cutoff"] = sum(1 for ev in g["top_evidence"] if ev["published_before_cutoff"])
        state.genetics.append(g)
        doc = Document(source_id=f"opentargets:{target['symbol']}", type="database",
                       title=f"Open Targets genetics: {target['symbol']} - coronary artery disorder",
                       text=json.dumps(g, ensure_ascii=False), url="https://platform.opentargets.org/target/" +
                       target["id"], pub_date=None, access="database")
        state.ingest.upsert_source(doc)
        entity = state.ingest.upsert_entity("target", target["symbol"])
        state.ingest.ingest_fact(case_id=state.case_id, entity_id=entity, key="genetic_association_score",
                                 value=g["genetic_association_score"], source_id=doc.source_id,
                                 quote=f'"genetic_association_score": {g["genetic_association_score"]}',
                                 quote_verified=True, agent=AGENT,
                                 attrs={"evidence_count": g["evidence_count"],
                                        "evidence_before_cutoff": g["evidence_before_cutoff"]})
        state.tracer.log(AGENT, "target_genetics", input_summary=target["symbol"],
                         output_summary=f"genetic score {g['genetic_association_score']}, "
                                        f"{g['evidence_count']} genetic evidence items "
                                        f"({g['evidence_before_cutoff']} of top items published before cut-off)",
                         source="Open Targets")


def gather_literature(state: RunState, extra_terms: str | None = None) -> list[Document]:
    """Find documents. Returns the newly added ones."""
    drug = state.drug.get("name") or state.request.get("drug") or ""
    acronym = state.trial.get("acronym") or ""
    if state.backtest:
        terms = [drug, acronym] + (extra_terms.split() if extra_terms else [])
        found = archive.search(state.docs, [t for t in terms if t], limit=50)
        state.tracer.log(AGENT, "search_archive", input_summary=", ".join(t for t in terms if t),
                         output_summary=f"{len(found)} archive documents match")
        return []

    names = [drug] + [a for a in state.drug.get("aliases", []) if len(a) > 4][:5]
    name_clause = " OR ".join(f'TITLE:"{n}" OR ABSTRACT:"{n}"' for n in names)
    if _skip(state, "europepmc", "literature search"):
        return []
    queries = [f"({name_clause})"]
    if acronym:
        queries.append(f'"{acronym}"')
    trial_types = ('PUB_TYPE:"Clinical Trial, Phase II" OR PUB_TYPE:"Clinical Trial, Phase III" OR '
                   'PUB_TYPE:"Randomized Controlled Trial"')
    tokens = sorted({t for n in names for t in re.split(r"[^A-Za-z0-9]+", n)
                     if (len(t) >= 5 or any(ch.isdigit() for ch in t)) and t.lower() not in GENERIC_TOKENS})
    if tokens:
        # Unquoted tokens catch code names typeset differently, e.g. "AKCEA-APO(a)-L Rx".
        queries.append(f"({' OR '.join(tokens[:8])}) AND ({trial_types})")
    if extra_terms:
        queries.append(f"({name_clause}) AND ({extra_terms})")
    seen = {d.source_id for d in state.docs}
    from app import config
    limit = config.max_literature_docs() + (2 if extra_terms else 0)
    candidates: dict[str, dict] = {}
    for q in queries:
        try:
            records = europepmc.search(q, as_of=state.as_of, page_size=25, tracer=state.tracer)
        except Exception as exc:
            state.warnings.append(f"Europe PMC search failed: {exc}")
            state.tracer.log(AGENT, "search_literature", input_summary=q, output_summary=str(exc), status="warning")
            continue
        relevant = [r for r in records if _mentions(r, names + ([acronym] if acronym else []))]
        for r in relevant:
            candidates.setdefault(r.get("pmid") or r.get("doi") or r["title"], r)
        state.tracer.log(AGENT, "search_literature", input_summary=q[:200],
                         output_summary=f"{len(records)} hits, {len(relevant)} about this drug/trial",
                         source="Europe PMC")
    ranked = sorted(candidates.values(), key=lambda r: _rank(r, names))
    added: list[Document] = []
    for record in ranked:
        if len([d for d in state.docs if d.type == "journal"]) + len(added) >= limit:
            break
        text = europepmc.full_text(record["pmcid"], tracer=state.tracer) \
            if record.get("pmcid") and record.get("is_open_access") else None
        doc = europepmc.to_document(record, text)
        if doc.source_id in seen:
            continue
        seen.add(doc.source_id)
        added.append(doc)
    state.tracer.log(AGENT, "select_documents", output_summary="; ".join(
        f"{d.pub_date} {d.title[:60]} [{d.access}]" for d in added)[:700], target_agent="CaseBuilder")
    state.docs.extend(added)
    return added


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _mentions(record: dict, names: list[str]) -> bool:
    """Name match that survives formatting, e.g. 'AKCEA-APO(a)-L Rx' vs 'AKCEA-APO(a)-LRx'."""
    haystack = _alnum(record["title"] + " " + record["abstract"])
    return any(len(_alnum(n)) >= 5 and _alnum(n) in haystack for n in names)


CARDIO = re.compile(r"cardio|coronary|atheroscl|myocard|vascular|lipid|cholesterol|LDL|lipoprotein|stroke|"
                    r"heart|hypercholest|dyslip", re.I)


def _rank(record: dict, names: list[str] | None = None):
    """Rule (v2): drug named in the title first; then trials and design papers before reviews;
    then cardiometabolic topic; then most recent."""
    types = " ".join(record.get("pub_type") or []).lower()
    title = record["title"].lower()
    in_title = bool(names) and any(len(_alnum(n)) >= 5 and _alnum(n) in _alnum(title) for n in names)
    if "randomized controlled trial" in types or "clinical trial" in types:
        tier = 0
    elif "design" in title or "rationale" in title or "trial" in title:
        tier = 1
    elif "review" in types or "review" in title or "seminar" in title:
        tier = 3
    else:
        tier = 2
    cardio = bool(CARDIO.search(record["title"] + " " + record.get("abstract", "")[:600]))
    year = int((record.get("first_pub_date") or "0000")[:4] or 0)
    return (not in_title, tier, not cardio, -year)


GENETIC_TERMS = ('(variant OR variants OR "loss-of-function" OR mendelian OR polymorphism OR genotype OR "genetic score") '
                 'AND (coronary OR "myocardial infarction" OR "cardiovascular")')


def gather_genetics_literature(state: RunState, limit: int = 3) -> list[Document]:
    """Genetic-association papers on each target, published before the cut-off (live mode)."""
    if state.backtest:
        return []
    if _skip(state, "genetics_literature", "genetics literature search") or _skip(state, "europepmc", "genetics literature search"):
        return []
    added = []
    for target in state.drug.get("targets", [])[:2]:
        q = f'(TITLE:"{target["symbol"]}" OR ABSTRACT:"{target["symbol"]}") AND {GENETIC_TERMS}'
        try:
            records = europepmc.search(q, as_of=state.as_of, page_size=10, tracer=state.tracer)
        except Exception as exc:
            state.warnings.append(f"genetics literature search failed: {exc}")
            continue
        records = [r for r in records if target["symbol"].lower() in (r["title"] + r["abstract"]).lower()][:limit]
        for r in records:
            doc = europepmc.to_document(r)
            doc.meta["purpose"] = "genetics"
            if doc.source_id not in {d.source_id for d in state.docs}:
                state.docs.append(doc)
                added.append(doc)
        state.tracer.log(AGENT, "search_genetics_literature", input_summary=q[:200],
                         output_summary=f"{len(records)} genetic-association papers on {target['symbol']} before cut-off",
                         source="Europe PMC")
    return added


def gather_announcements(state: RunState) -> list[Document]:
    """Find the sponsor's result announcement in SEC filings when the user did not paste one (live autopsies)."""
    from datetime import date, timedelta
    from app.tools import sec
    if state.backtest or state.request.get("readout_text") or state.request.get("mode") == "forecast":
        return []
    if state.mode == "forecast" and state.trial.get("status") not in ("COMPLETED", "TERMINATED"):
        return []
    if _skip(state, "sec", "announcement search"):
        return []
    end = state.as_of or date.today()
    completion = state.trial.get("primary_completion")
    try:
        start = date.fromisoformat((completion + "-01")[:10]) - timedelta(days=30) if completion else end - timedelta(days=365)
    except ValueError:
        start = end - timedelta(days=365)
    names = [state.trial.get("acronym"), state.drug.get("name")] + [a for a in state.drug.get("aliases", []) if len(a) > 5][:2]
    names = [n for n in names if n]
    try:
        hits = sec.search(names, start, end, tracer=state.tracer)
    except Exception as exc:
        state.warnings.append(f"SEC EDGAR search failed: {exc}")
        state.tracer.log(AGENT, "search_announcements", output_summary=str(exc)[:200], status="warning")
        return []
    added = []
    for hit in hits[:4]:
        try:
            doc = sec.fetch(hit, tracer=state.tracer)
        except Exception:
            continue
        if sec.announces_result(doc, names) and doc.source_id not in {d.source_id for d in state.docs}:
            state.docs.append(doc)
            added.append(doc)
        if len(added) >= 2:
            break
    state.tracer.log(AGENT, "search_announcements", input_summary=f"{names} {start}..{end}",
                     output_summary=f"{len(hits)} filings, {len(added)} announce a result: "
                                    + "; ".join(d.title for d in added)[:300], source="SEC EDGAR")
    if added and state.mode == "forecast":
        state.mode = "autopsy"
        state.tracer.log(AGENT, "switch_mode", output_summary="result announcement found in SEC filings: autopsy mode")
    return added


def excerpt(doc: Document) -> str:
    """Abstract/head plus every passage with numbers and key terms, capped (deterministic)."""
    if len(doc.text) <= EXCERPT_CHARS:
        return doc.text
    head = doc.text[:2500]
    passages = re.split(r"(?<=[.;|])\s+(?=[A-Z(])", doc.text[2500:])
    kept, size = [], len(head)
    for p in passages:
        if NUMBER.search(p) and KEY_TERMS.search(p):
            if size + len(p) > EXCERPT_CHARS:
                break
            kept.append(p.strip())
            size += len(p)
    return head + "\n...\n" + " ".join(kept)


def extract_all(state: RunState, docs: list[Document]):
    targets = [d for d in docs if d.type != "database"]
    for d in targets:
        state.ingest.upsert_source(d)

    def work(doc: Document):
        return doc, _extract(state, doc)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(work, targets))
    pending = [outcome for _, outcome in results if isinstance(outcome, generator.PendingModelCall)]
    if pending:
        raise generator.PendingBatch(pending)


DOWN = re.compile(r"\b(decreas|reduc|lower|fell|fall|declin|drop|cut)\w*", re.I)
UP = re.compile(r"\b(increas|rais|elevat|ros[ei]|higher|gain)\w*", re.I)


def change_direction(quote: str, value, window: int = 160) -> int:
    """+1 or -1 when the quote says in words which way the number moved ("decreased LDL-C by 8%"), else 0.
    The nearest direction word before the number decides; an explicit sign in the quote always wins."""
    try:
        magnitude = abs(float(value))
    except (TypeError, ValueError):
        return 0
    text = f"{magnitude:g}"
    for m in re.finditer(rf"([-+−]?)\b{re.escape(text)}(?![\d.])", quote):
        if m.group(1):
            return -1 if m.group(1) in "-−" else 1
        # "a 27.2% decrease": a direction word right after the number (within three words) decides first.
        after = re.match(r"\s*(?:%|percent|mg/dl|mmol/l|nmol/l)?\s*(?:\S+\s+){0,2}?(\S+)", quote[m.end():m.end() + 60], re.I)
        if after:
            word = after.group(1)
            if DOWN.match(word):
                return -1
            if UP.match(word):
                return 1
        before = quote[max(0, m.start() - window):m.start()]
        downs = [x.end() for x in DOWN.finditer(before)]
        ups = [x.end() for x in UP.finditer(before)]
        if downs or ups:
            return -1 if (max(downs) if downs else -1) > (max(ups) if ups else -1) else 1
    return 0


def _number_in(quote: str, value) -> bool:
    """True if the change's own number is written in the quote (not, say, a difference computed from two levels)."""
    try:
        text = f"{abs(float(value)):g}"
    except (TypeError, ValueError):
        return False
    return re.search(rf"(?<![\d.]){re.escape(text)}(?![\d.])", quote) is not None


def sentence_before(document_text: str, quote: str, chars: int = 400) -> str:
    """The start of the quote's sentence in the document, when the quote begins mid-sentence ("and by 63% ...",
    after "reduced LDL-C by 66%"). Normalised like the quote check; empty if the quote is not found."""
    doc, q = _normalize(document_text), _normalize(quote)
    at = doc.find(q)
    if at <= 0:
        return ""
    return re.split(r"[.;:]\s", doc[max(0, at - chars):at])[-1]


def _extract(state: RunState, doc: Document):
    genetics = (doc.meta or {}).get("purpose") == "genetics"
    system = prompts.GENETICS_SYSTEM if genetics else prompts.EXTRACTION_SYSTEM
    prompt = prompts.EXTRACTION_PROMPT.format(
        trial_label=state.trial_label, drug=state.drug.get("name"), doc_title=doc.title, doc_type=doc.type,
        pub_date=doc.pub_date or "unknown", source_id=doc.source_id, text=excerpt(doc))
    try:
        data, response = generator.generate_json("extraction", system, prompt,
                                                 tag=f"{state.case_id}:extract:{doc.source_id}", agent="Extraction",
                                                 list_key="facts")
    except generator.PendingModelCall as exc:
        return exc
    except (ValueError, json.JSONDecodeError) as exc:
        state.tracer.log(AGENT, "extract_facts", input_summary=doc.source_id, output_summary=f"unparseable: {exc}",
                         status="warning")
        return None
    state.record_model("Extraction", response)
    kept = rejected = 0
    for fact in data.get("facts", []):
        quote = fact.get("quote") or ""
        ok = verify_quote(doc.text, quote)
        attrs = fact.get("attrs") or {}
        if fact.get("key") == "biomarker_change" and ok and attrs.get("kind") in ("percent", "absolute", None):
            # Code, not the model, sets the direction of a change: the quote's own words decide, and when the quote
            # has none ("and by 63% vs placebo"), the words earlier in the same sentence of the document do.
            direction, said_by = change_direction(quote, fact.get("value")), "quote"
            if not direction and _number_in(quote, fact.get("value")) and not (DOWN.search(quote) or UP.search(quote)):
                before = sentence_before(doc.text, quote)
                if before:
                    direction, said_by = change_direction(f"{before} {_normalize(quote)}", fact.get("value"), window=480), "sentence"
            try:
                v = float(fact.get("value"))
                if direction and (v < 0) != (direction < 0):
                    fact["value"] = -v
                    attrs["sign_set_by_code"] = f"{said_by} says {'decrease' if direction < 0 else 'increase'}"
            except (TypeError, ValueError):
                pass
        if fact.get("key") == "genetic_association":
            entity = state.ingest.upsert_entity("target", attrs.get("gene") or "unknown")
        elif fact.get("key") in ("biomarker_change", "baseline_level"):
            entity = state.ingest.upsert_entity("biomarker", attrs.get("biomarker") or "unknown")
        else:
            entity = state.ingest.upsert_entity("trial", state.trial.get("nct_id") or "unknown")
        state.ingest.ingest_fact(case_id=state.case_id, entity_id=entity, key=fact.get("key", "unknown"),
                                 value=fact.get("value"), unit=fact.get("unit", ""), attrs=attrs,
                                 source_id=doc.source_id, quote=quote, quote_verified=ok, agent=AGENT)
        kept += ok
        rejected += not ok
    state.tracer.log(AGENT, "extract_facts", input_summary=f"{doc.source_id} ({doc.access}, {doc.pub_date})",
                     output_summary=f"{kept} facts verified against the document text, {rejected} rejected "
                                    f"(quote not found)", confidence=round(kept / max(kept + rejected, 1), 2),
                     target_agent="WorldModel", model=f"{response.provider}:{response.model}", cached=response.cached)
    return None
