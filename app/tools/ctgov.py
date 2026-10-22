"""ClinicalTrials.gov API v2.

The public API has no record history, so for a past `as_of` date we return a
"design-only" view: status, stop reason, completion dates and results are removed
because they could reveal the outcome.
"""
from __future__ import annotations

import re
from datetime import date

from app.tools import http
from app.tools.documents import Document

API = "https://clinicaltrials.gov/api/v2/studies"


def get_study(nct_id: str, tracer=None) -> dict:
    return http.get_json(f"{API}/{nct_id}", tracer=tracer, source="ClinicalTrials.gov")


def search(query_intr: str | None = None, query_cond: str | None = None, advanced: str | None = None,
           page_size: int = 50, tracer=None) -> list[dict]:
    params = {"pageSize": str(page_size), "fields": "NCTId,BriefTitle,Acronym,OverallStatus,Phase,"
              "InterventionName,Condition,EnrollmentCount,PrimaryCompletionDate,LeadSponsorName"}
    if query_intr:
        params["query.intr"] = query_intr
    if query_cond:
        params["query.cond"] = query_cond
    if advanced:
        params["filter.advanced"] = advanced
    body = http.get_json(API, params=params, tracer=tracer, source="ClinicalTrials.gov")
    return body.get("studies", [])


def summarize(study: dict) -> dict:
    p = study.get("protocolSection", {})
    ident, status, design = p.get("identificationModule", {}), p.get("statusModule", {}), p.get("designModule", {})
    arms = p.get("armsInterventionsModule", {})
    return {
        "nct_id": ident.get("nctId"),
        "acronym": ident.get("acronym"),
        "title": ident.get("briefTitle"),
        "official_title": ident.get("officialTitle"),
        "sponsor": p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name"),
        "status": status.get("overallStatus"),
        "why_stopped": status.get("whyStopped"),
        "start_date": status.get("startDateStruct", {}).get("date"),
        "primary_completion": status.get("primaryCompletionDateStruct", {}).get("date"),
        "first_posted": status.get("studyFirstPostDateStruct", {}).get("date"),
        "phases": design.get("phases", []),
        "enrollment": design.get("enrollmentInfo", {}).get("count"),
        "enrollment_type": design.get("enrollmentInfo", {}).get("type"),
        "interventions": [{"name": i.get("name"), "type": i.get("type"), "other_names": i.get("otherNames", [])}
                          for i in arms.get("interventions", [])],
        "conditions": p.get("conditionsModule", {}).get("conditions", []),
        "primary_outcomes": [o.get("measure") + (f" [{o.get('timeFrame')}]" if o.get("timeFrame") else "")
                             for o in p.get("outcomesModule", {}).get("primaryOutcomes", [])],
        "has_results": study.get("hasResults", False),
    }


def to_document(study: dict, as_of: date | None = None) -> Document:
    s = summarize(study)
    p = study.get("protocolSection", {})
    past_view = as_of is not None and as_of < date.today()
    lines = [
        f"ClinicalTrials.gov record {s['nct_id']} ({s['acronym'] or ''})",
        f"Title: {s['title']}",
        f"Official title: {s['official_title']}",
        f"Sponsor: {s['sponsor']}",
        f"Phase: {', '.join(s['phases'])}",
        f"Conditions: {'; '.join(s['conditions'])}",
        "Interventions: " + "; ".join(f"{i['name']} ({i['type']})" for i in s["interventions"]),
        f"Start date: {s['start_date']}",
        f"Enrollment: {s['enrollment']} ({s['enrollment_type']})",
        "Primary outcome(s): " + " | ".join(s["primary_outcomes"]),
    ]
    eligibility = p.get("eligibilityModule", {}).get("eligibilityCriteria", "")
    if eligibility:
        lines.append("Eligibility criteria:\n" + eligibility[:6000])
    description = p.get("descriptionModule", {}).get("briefSummary", "")
    if description:
        if past_view:
            description = _strip_outcome_sentences(description)
        lines.append("Brief summary: " + description[:3000])
    if not past_view:
        lines += [f"Overall status: {s['status']}", f"Why stopped: {s['why_stopped'] or '-'}",
                  f"Primary completion: {s['primary_completion']}", f"Has posted results: {s['has_results']}"]
        lines += _results_text(study)
    return Document(
        source_id=f"ctgov:{s['nct_id']}" + (f"@{as_of.isoformat()}" if past_view else ""),
        type="registry", title=f"ClinicalTrials.gov {s['nct_id']} {s['acronym'] or ''}".strip(),
        text="\n".join(lines), url=f"https://clinicaltrials.gov/study/{s['nct_id']}",
        pub_date=_iso(s["first_posted"]), access="registry",
        meta={"design_only_view": past_view, "summary": s},
    )


def _results_text(study: dict) -> list[str]:
    results = study.get("resultsSection", {})
    out = []
    for measure in results.get("outcomeMeasuresModule", {}).get("outcomeMeasures", [])[:6]:
        out.append(f"Posted result - {measure.get('type')}: {measure.get('title')} ({measure.get('timeFrame', '')})")
        for analysis in measure.get("analyses", [])[:3]:
            parts = [f"{k}={analysis.get(k)}" for k in ("paramType", "paramValue", "ciLowerLimit", "ciUpperLimit",
                                                         "pValue", "statisticalMethod") if analysis.get(k)]
            out.append("  analysis: " + ", ".join(parts))
    return out


OUTCOME_HINT = re.compile(r"terminat|discontinu|stopped|halted|futility|safety finding|results? (show|demonstrat)|"
                          r"did not meet|met (its|the) primary|was completed", re.I)


def _strip_outcome_sentences(text: str) -> str:
    """Records are edited after the fact; in a past view drop sentences that hint at the outcome."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = [s for s in sentences if not OUTCOME_HINT.search(s)]
    return " ".join(kept)


def _iso(value: str | None) -> str | None:
    if not value:
        return None
    return value if len(value) == 10 else (value + "-01" if len(value) == 7 else value)
