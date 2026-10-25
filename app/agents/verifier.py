"""Verifier agent: an independent check of the calculator's key numbers against the sources of record, through Zetaris.

Nemotron writes one Lightning SQL query per number against two Zetaris tables (the trial's registry record and the
cited papers' Europe PMC records), code runs the queries in Zetaris, and Nemotron judges each number against the rows
that came back: confirmed, not found, or contradicted. A contradiction goes to the critic as a high-severity issue.

Without Zetaris the check cannot run, and the run says so: the explanation's confidence drops one level. The data
itself is still read directly, so the product keeps working; it just loses its second, independent check.

The model only ever sees the table aliases `registry` and `papers`; code maps them to the real Zetaris session tables,
so prompts stay identical between runs and recorded outputs replay offline. Query results are recorded in
data/sample_cache/zetaris/ and replayed in SAMPLE_MODE.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from urllib.parse import urlencode

from app import config
from app.agents import prompts
from app.agents.state import RunState
from app.llm import generator
from app.tools import zetaris

CTGOV_API = "https://clinicaltrials.gov/api/v2/studies"
EPMC_API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
SQL_CACHE = config.DATA / "sample_cache" / "zetaris"
MAX_CLAIMS = 8
MAX_ROWS = 20
ROW_CHARS = 700
CHECKED_SOURCES = ("pmid:", "pmcid:", "ctgov:")


class UnsafeSQL(ValueError):
    pass


# --- what to check -------------------------------------------------------------------------------

def claims(state: RunState) -> list[dict]:
    """The numbers the calculator runs on, each with the verified fact it came from (papers and registry only)."""
    facts = {f["fact_id"]: f for f in state.context.facts(state.case_id)}
    m = state.mechanism or {}
    wanted = []
    for c in m.get("changes") or []:
        for fid in c.get("fact_ids") or []:
            wanted.append((fid, f"{c.get('biomarker')} change used by the calculator"))
    for key, label in (("events_fact_ids", "number of primary events"), ("followup_fact_ids", "follow-up")):
        for fid in m.get(key) or []:
            wanted.append((fid, label))
    out, seen = [], set()
    for fid, label in wanted:
        f = facts.get(fid)
        if not f or fid in seen or not f["source_id"].startswith(CHECKED_SOURCES):
            continue
        seen.add(fid)
        out.append({"claim": f"c{len(out) + 1}", "fact_id": fid, "used_as": label, "key": f["key"],
                    "value": f["value"], "unit": f.get("unit") or "", "attrs": f.get("attrs") or {},
                    "source_id": f["source_id"], "quote": (f.get("quote") or "")[:300]})
        if len(out) == MAX_CLAIMS:
            break
    return out


BIOC = "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pubmed.cgi/BioC_json"


def pubmed_papers_url(state: RunState, items: list[dict]) -> str | None:
    """The cited papers' PubMed abstracts as BioC JSON, when Europe PMC cannot be read: one URL for all PMIDs (a PMC
    source is looked up through its document's PMID)."""
    pmids = set()
    meta = {d.source_id: d.meta or {} for d in state.docs}
    for c in items:
        kind, _, ident = c["source_id"].partition(":")
        if kind == "pmid":
            pmids.add(ident)
        elif kind == "pmcid" and meta.get(c["source_id"], {}).get("pmid"):
            pmids.add(str(meta[c["source_id"]]["pmid"]))
    return f"{BIOC}/{','.join(sorted(pmids))}/unicode" if pmids else None


def tables(state: RunState, items: list[dict]) -> dict[str, str]:
    """Alias -> source URL for the Zetaris tables these claims need."""
    out = {}
    nct = state.trial.get("nct_id")
    if nct:
        out["registry"] = f"{CTGOV_API}/{nct}"
    ids = []
    for c in items:
        kind, _, ident = c["source_id"].partition(":")
        if kind == "pmid":
            ids.append(f"EXT_ID:{ident}")
        elif kind == "pmcid":
            ids.append(f"PMCID:{ident}")
    if ids:
        query = " OR ".join(sorted(set(ids)))
        out["papers"] = f"{EPMC_API}?{urlencode({'query': query, 'format': 'json', 'resultType': 'core', 'pageSize': '25'})}"
    return out


# --- SQL safety ----------------------------------------------------------------------------------

# Time machine: with an evidence cut-off (backtests), registry fields that change once a trial reads out are off limits,
# and so is SELECT *: the Verifier may only confirm what was knowable on the cut-off date.
_AFTER_READOUT = re.compile(r"\b(resultsSection|hasResults|statusModule|derivedSection|documentSection|enrollmentInfo|"
                            r"annotationSection|citedByCount|commentCorrectionList|dateOfRevision)\b", re.I)
_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|create|alter|grant|revoke|truncate|merge|call|exec|execute|"
                        r"set|use|show|describe|refresh|cache|uncache|load|add|msck|lightning)\b", re.I)
# A container that holds a forbidden field returns it whole: protocolSection holds statusModule, designModule holds
# enrollmentInfo. Selecting the container (or wrapping it in to_json) is a way round the field list.
_CONTAINERS = re.compile(r"(?<![\w.])(?:(?:registry|papers)\.)?(?:protocolSection|protocolSection\.designModule)(?![\w.])",
                         re.I)
_TABLE_REF = re.compile(r"\b(from|join)\s+([A-Za-z_][\w.]*)", re.I)
# FROM inside a function is not a table: EXTRACT(year FROM d), TRIM(BOTH 'x' FROM s), SUBSTRING(s FROM 2)
_FROM_IN_CALL = re.compile(r"\b(extract|trim|substring|overlay|position)\s*\([^()]*\)", re.I)


def safe_sql(sql: str, real: dict[str, str], cutoff: bool = False) -> str:
    """A single read-only SELECT over this analysis's tables only, with the aliases mapped to the real tables.
    With an evidence cut-off, fields that only exist after the readout and SELECT * are rejected."""
    s = (sql or "").strip().rstrip(";").strip()
    if not re.match(r"(?is)^select\b", s):
        raise UnsafeSQL("only a single SELECT statement is allowed")
    if ";" in s or "--" in s or "/*" in s:
        raise UnsafeSQL("one statement only, no comments")
    if _FORBIDDEN.search(re.sub(r"'[^']*'", "''", s)):
        raise UnsafeSQL("statement contains a keyword that is not allowed")
    if "`" in s or '"' in s:
        raise UnsafeSQL("quoted identifiers are not allowed")
    bare = _FROM_IN_CALL.sub("f()", re.sub(r"'[^']*'", "''", s))
    if cutoff:
        # the field names are checked in the statement as written too, so protocolSection['statusModule'] or
        # get_json_object(x, '$.statusModule') cannot hide one inside a string
        starred = re.sub(r"(?i)\bcount\s*\(\s*\*\s*\)", "count(1)", bare)
        if _AFTER_READOUT.search(s) or _CONTAINERS.search(bare) or "*" in starred:
            raise UnsafeSQL("with an evidence cut-off only design-time fields may be read (no results, status, "
                            "enrollment, whole sections or SELECT *)")
    for clause in re.findall(r"(?is)\bfrom\b(.*?)(?=\bwhere\b|\bgroup\b|\border\b|\blimit\b|\bhaving\b|\bunion\b|"
                             r"\bjoin\b|\bleft\b|\binner\b|\bright\b|\bcross\b|\)|$)", bare):
        if "," in clause:
            raise UnsafeSQL("read the tables with JOIN, not a comma list")
    refs = _TABLE_REF.findall(bare)
    if not refs:
        raise UnsafeSQL("statement reads no table")
    for _, name in refs:
        if name not in real:
            raise UnsafeSQL(f"unknown table {name}: use {', '.join(sorted(real))}")
    calls = {}
    s = _FROM_IN_CALL.sub(lambda m: calls.setdefault(f"__call{len(calls)}__", m.group(0)) and f"__call{len(calls) - 1}__", s)
    s = _TABLE_REF.sub(lambda m: f"{m.group(1)} session.{real[m.group(2)]}", s)
    for k, v in calls.items():
        s = s.replace(k, v)
    if not re.search(r"(?i)\blimit\s+\d+\s*$", s):
        s += f" LIMIT {MAX_ROWS}"
    return s


# --- running queries -------------------------------------------------------------------------------

def _cache_key(alias: str, url: str, sql: str) -> str:
    return hashlib.sha256(json.dumps([alias, url, " ".join(sql.split())]).encode()).hexdigest()[:20]


def _cached(key: str) -> dict | None:
    path = SQL_CACHE / f"{key}.json"
    return json.loads(path.read_text()) if path.exists() else None


def _store(key: str, record: dict):
    SQL_CACHE.mkdir(parents=True, exist_ok=True)
    (SQL_CACHE / f"{key}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))


class _Session:
    """Zetaris session tables for one verification pass (created lazily, dropped at the end)."""

    def __init__(self, urls: dict[str, str]):
        self.urls, self.real = urls, {}

    def table(self, alias: str) -> str:
        if alias not in self.real:
            self.real[alias] = zetaris._session_table(self.urls[alias])
        return self.real[alias]

    def close(self):
        for name in self.real.values():
            zetaris._drop(name)


def _alias_of(sql: str, urls: dict) -> str | None:
    refs = [n for _, n in _TABLE_REF.findall(re.sub(r"'[^']*'", "''", sql or ""))]
    return next((n for n in refs if n in urls), None)


def run_query(sql: str, urls: dict[str, str], session: _Session | None, cutoff: bool = False) -> dict:
    """Run one model-written query. Returns {sql, rows, error, cached}; never raises for a bad query."""
    alias = _alias_of(sql, urls)
    if alias is None:
        return {"sql": sql, "rows": [], "error": f"query must read one of: {', '.join(sorted(urls))}"}
    try:
        safe_sql(sql, {a: a for a in urls}, cutoff)  # the same rule offline and live; nothing unsafe reaches Zetaris
    except UnsafeSQL as exc:
        return {"sql": sql, "rows": [], "error": f"rejected: {exc}"}
    key = _cache_key(alias, urls[alias], sql)
    hit = _cached(key)
    if hit is not None:
        return {"sql": sql, "rows": hit.get("rows") or [], "error": hit.get("error"), "cached": True}
    if session is None:
        return {"sql": sql, "rows": [], "error": "not recorded", "missing": True}
    try:
        real = {a: session.table(a) for a in urls if re.search(rf"\b{a}\b", sql)}
        statement = safe_sql(sql, real, cutoff)
        try:
            rows = zetaris.sql(statement, limit=MAX_ROWS)
        except zetaris.ZetarisError as exc:
            if "429" not in str(exc):
                raise
            time.sleep(6)   # the source behind the table asked us to slow down (PubMed): one pause, one retry
            rows = zetaris.sql(statement, limit=MAX_ROWS)
        record = {"alias": alias, "source": urls[alias], "sql": sql, "rows": rows[:MAX_ROWS], "error": None}
    except zetaris.ZetarisError as exc:
        record = {"alias": alias, "source": urls[alias], "sql": sql, "rows": [], "error": str(exc)[:300]}
    _store(key, record)
    return {"sql": sql, "rows": record["rows"], "error": record["error"], "cached": False}


def _rows_text(result: dict) -> str:
    if result.get("error"):
        return f"ERROR: {result['error']}"
    text = json.dumps(result["rows"], ensure_ascii=False)
    return text if len(text) <= ROW_CHARS else text[:ROW_CHARS] + " …(truncated)"


# --- the agent -------------------------------------------------------------------------------------

def run(state: RunState):
    items = claims(state)
    if not items:
        state.verification = {"status": "skipped", "reason": "no calculator number comes from a paper or the registry",
                              "results": []}
        state.tracer.log("Verifier", "verify_inputs", output_summary="nothing to check", target_agent="Critic")
        return
    urls = tables(state, items)
    cutoff = state.as_of is not None
    live = zetaris.enabled()
    if not live and not config.sample_mode():
        _unavailable(state, items, "Zetaris is not configured or switched off")
        return
    session = _Session(urls) if live else None
    try:
        lost, lost_reason = [], ""
        if live:
            failed = {}
            for alias in list(urls):
                try:
                    session.table(alias)
                except zetaris.ZetarisError as exc:
                    fallback = pubmed_papers_url(state, items) if alias == "papers" else None
                    if fallback:
                        try:
                            urls[alias] = session.urls[alias] = fallback
                            session.table(alias)
                            state.tracer.log("Verifier", "source_fallback", input_summary="papers",
                                             output_summary=f"Europe PMC table could not be read ({str(exc)[:120]}); "
                                                            f"PubMed abstracts (BioC) used instead",
                                             status="warning", via="zetaris", source="PubMed")
                            continue
                        except zetaris.ZetarisError as exc2:
                            exc = exc2
                    failed[alias] = str(exc)
            if len(failed) == len(urls):
                _unavailable(state, items, f"Zetaris unreachable: {next(iter(failed.values()))}"[:200])
                return
            if failed:
                # One source is down (e.g. Europe PMC): check what the other tables can show, and say which numbers
                # were not checked, instead of giving up on all of them.
                lost = [c for c in items if _table_of(c) in failed]
                items = [c for c in items if c not in lost]
                urls = {a: u for a, u in urls.items() if a not in failed}
                lost_reason = "; ".join(f"{a} table could not be read: {e}" for a, e in failed.items())[:300]
                state.tracer.log("Verifier", "source_unavailable", output_summary=f"{lost_reason}; {len(lost)} numbers "
                                 f"from it are not checked", status="warning", via="zetaris", target_agent="Critic")
                if not items:
                    _unavailable(state, lost, lost_reason)
                    return
        state.tracer.log("Verifier", "register_tables",
                         output_summary="; ".join(f"{a} = Zetaris REST session table over {u.split('?')[0]}"
                                                  for a, u in urls.items()),
                         via="zetaris", target_agent="Zetaris")
        checks = _write_queries(state, items, urls, cutoff)
        results = {}
        for chk in checks:
            res = run_query(chk.get("sql", ""), urls, session, cutoff)
            results[chk.get("claim")] = res
            if res.get("missing"):
                _unavailable(state, items, "query results not recorded for offline replay")
                return
            state.tracer.log("Verifier", "query_zetaris", input_summary=chk.get("sql", ""),
                             output_summary=(f"{len(res['rows'])} rows" if not res.get("error") else f"error: {res['error']}")
                             + (" (recorded)" if res.get("cached") else ""),
                             status="warning" if res.get("error") else "success", via="zetaris", target_agent="Verifier")
        retry = [c for c in checks if results.get(c.get("claim"), {}).get("error")]
        if retry:
            for chk in _repair_queries(state, items, urls, retry, results, cutoff):
                res = run_query(chk.get("sql", ""), urls, session, cutoff)
                if res.get("missing"):
                    _unavailable(state, items, "query results not recorded for offline replay")
                    return
                results[chk.get("claim")] = res
                state.tracer.log("Verifier", "query_zetaris", input_summary=chk.get("sql", ""),
                                 output_summary=(f"{len(res['rows'])} rows" if not res.get("error") else f"error: {res['error']}"),
                                 status="warning" if res.get("error") else "success", via="zetaris",
                                 target_agent="Verifier", retry_count=1)
    finally:
        if session:
            session.close()
    _judge(state, items, checks, results)
    if lost:
        state.verification["results"] += [
            {"fact_id": c["fact_id"], "claim": c["claim"], "used_as": c["used_as"], "key": c["key"], "value": c["value"],
             "unit": c["unit"], "source_id": c["source_id"], "status": "not_checked", "note": lost_reason} for c in lost]
        state.verification["partial"] = lost_reason


def _table_of(claim: dict) -> str:
    """The Zetaris table a claim is checked in: the registry record or the papers."""
    return "registry" if claim["source_id"].startswith("ctgov:") else "papers"


def _schema(urls: dict, cutoff: bool = False) -> str:
    parts = []
    if "registry" in urls:
        parts.append(prompts.VERIFIER_REGISTRY_SCHEMA_CUTOFF if cutoff else prompts.VERIFIER_REGISTRY_SCHEMA)
    if "papers" in urls:
        parts.append(prompts.VERIFIER_PAPERS_SCHEMA_PUBMED if BIOC in urls["papers"] else prompts.VERIFIER_PAPERS_SCHEMA)
    return "\n\n".join(parts)


def _claims_text(items: list[dict]) -> str:
    return "\n".join(f"{c['claim']} | {c['used_as']} | {c['key']} = {c['value']} {c['unit']} | "
                     f"{json.dumps(c['attrs'], sort_keys=True, ensure_ascii=False)} | source {c['source_id']} | "
                     f"quote: \"{c['quote']}\"" for c in items)


def _write_queries(state: RunState, items: list[dict], urls: dict, cutoff: bool = False) -> list[dict]:
    prompt = prompts.VERIFIER_QUERY_PROMPT.format(trial_label=state.trial_label, nct=state.trial.get("nct_id"),
                                                  schema=_schema(urls, cutoff), claims=_claims_text(items))
    data, response = generator.generate_json("critic", prompts.VERIFIER_QUERY_SYSTEM, prompt,
                                             tag=f"{state.case_id}:verify-sql", agent="Verifier", list_key="checks")
    state.record_model("Verifier", response)
    ids = {c["claim"] for c in items}
    checks = [c for c in data.get("checks") or [] if isinstance(c, dict) and c.get("claim") in ids][:MAX_CLAIMS]
    state.tracer.log("Verifier", "write_sql", output_summary=f"{len(checks)} queries for {len(items)} numbers",
                     target_agent="Zetaris", model=f"{response.provider}:{response.model}", cached=response.cached)
    return checks


def _repair_queries(state: RunState, items, urls, failed: list[dict], results: dict, cutoff: bool = False) -> list[dict]:
    lines = "\n".join(f"{c.get('claim')} | {c.get('sql')} | {results[c.get('claim')]['error']}" for c in failed)
    prompt = prompts.VERIFIER_REPAIR_PROMPT.format(schema=_schema(urls, cutoff), claims=_claims_text(items), failed=lines)
    data, response = generator.generate_json("critic", prompts.VERIFIER_QUERY_SYSTEM, prompt,
                                             tag=f"{state.case_id}:verify-sql-repair", agent="Verifier", list_key="checks")
    state.record_model("Verifier", response)
    wanted = {c.get("claim") for c in failed}
    return [c for c in data.get("checks") or [] if isinstance(c, dict) and c.get("claim") in wanted]


def _judge(state: RunState, items, checks, results):
    sql_by_claim = {c.get("claim"): c.get("sql") for c in checks}
    lines = "\n".join(f"{c['claim']} | SQL: {results.get(c['claim'], {}).get('sql') or sql_by_claim.get(c['claim']) or '(none)'}"
                      f" | rows: {_rows_text(results[c['claim']]) if c['claim'] in results else '(no query)'}"
                      for c in items)
    prompt = prompts.VERIFIER_JUDGE_PROMPT.format(trial_label=state.trial_label, claims=_claims_text(items), results=lines)
    data, response = generator.generate_json("critic", prompts.VERIFIER_JUDGE_SYSTEM, prompt,
                                             tag=f"{state.case_id}:verify-judge", agent="Verifier", list_key="results")
    state.record_model("Verifier", response)
    by_claim = {r.get("claim"): r for r in data.get("results") or [] if isinstance(r, dict)}
    out = []
    for c in items:
        r = by_claim.get(c["claim"], {})
        res = results.get(c["claim"], {})
        status = r.get("status") if r.get("status") in ("confirmed", "not_found", "contradicts") else "not_found"
        if status != "not_found" and not res.get("rows"):
            status = "not_found"   # code rule: no rows came back, so nothing can be confirmed or contradicted
        out.append({"fact_id": c["fact_id"], "claim": c["claim"], "used_as": c["used_as"], "key": c["key"],
                    "value": c["value"], "unit": c["unit"], "source_id": c["source_id"], "status": status,
                    "found": str(r.get("found") or "")[:300], "note": str(r.get("note") or "")[:300],
                    "sql": res.get("sql") or sql_by_claim.get(c["claim"]), "rows": len(res.get("rows") or [])})
    counts = {s: sum(1 for o in out if o["status"] == s) for s in ("confirmed", "not_found", "contradicts")}
    state.verification = {"status": "done", "via": "zetaris", "results": out, "counts": counts,
                          "queries": len(results)}
    for o in out:
        if o["status"] == "contradicts":
            state.auto_issues.append(f"Verifier (Zetaris SQL): {o['used_as']} {o['value']} {o['unit']} "
                                     f"({o['fact_id']}, {o['source_id']}) contradicts the source: {o['found'] or o['note']}")
    state.tracer.log("Verifier", "verify_inputs",
                     output_summary=f"{counts['confirmed']} confirmed, {counts['not_found']} not found, "
                                    f"{counts['contradicts']} contradicted, checked in Zetaris",
                     target_agent="Critic", status="needs_revision" if counts["contradicts"] else "success",
                     model=f"{response.provider}:{response.model}", cached=response.cached, via="zetaris")


def _unavailable(state: RunState, items: list[dict], reason: str):
    state.verification = {"status": "unavailable", "reason": reason, "results": [
        {"fact_id": c["fact_id"], "claim": c["claim"], "used_as": c["used_as"], "key": c["key"], "value": c["value"],
         "unit": c["unit"], "source_id": c["source_id"], "status": "not_checked"} for c in items]}
    state.tracer.log("Verifier", "verification_unavailable",
                     output_summary=f"{len(items)} numbers not independently verified: {reason}",
                     status="warning", target_agent="Critic")


def summary(state: RunState) -> str:
    """The verification result as text for the critic's prompt."""
    v = state.verification or {}
    if v.get("status") == "unavailable":
        return f"- not run: {v.get('reason')}. The numbers were not independently verified."
    if v.get("partial"):
        missing = sum(1 for r in v.get("results") or [] if r.get("status") == "not_checked")
        prefix = f"- {missing} numbers not checked ({v['partial']}).\n"
    else:
        prefix = ""
    if v.get("status") != "done":
        return "- nothing to check"
    return prefix + "\n".join(f"- {r['fact_id']} ({r['used_as']}, {r['value']} {r['unit']}): {r['status']}"
                     + (f"; found: {r['found']}" if r.get("found") else "") + (f"; {r['note']}" if r.get("note") else "")
                     for r in v["results"])
