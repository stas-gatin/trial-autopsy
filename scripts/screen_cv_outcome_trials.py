"""Screen ClinicalTrials.gov for phase 3 cardiovascular outcome drug trials.

Writes data/screening/cv_outcome_trials.json with three groups:
  fresh     - completed/terminated, primary completion since 2025-07-01
  large_done - >=3000 enrolled, completed/terminated
  large_active - >=3000 enrolled, recruiting or active
Stdlib only, no API key needed.
"""
import json
import pathlib
import urllib.parse
import urllib.request

API = "https://clinicaltrials.gov/api/v2/studies"
OUTCOME_QUERY = '"major adverse cardiovascular events" OR "cardiovascular death" OR MACE'
PHASE3_DRUG = (
    "AREA[Phase]PHASE3 AND AREA[StudyType]INTERVENTIONAL "
    "AND AREA[InterventionType](DRUG OR BIOLOGICAL)"
)
FIELDS = ",".join([
    "NCTId", "BriefTitle", "Acronym", "OverallStatus", "WhyStopped", "LeadSponsorName",
    "InterventionName", "InterventionType", "Condition", "EnrollmentCount",
    "StartDate", "PrimaryCompletionDate", "CompletionDate", "HasResults",
])
GROUPS = {
    "fresh": " AND AREA[OverallStatus](COMPLETED OR TERMINATED)"
             " AND AREA[PrimaryCompletionDate]RANGE[2025-07-01,MAX]",
    "large_done": " AND AREA[EnrollmentCount]RANGE[3000,MAX]"
                  " AND AREA[OverallStatus](COMPLETED OR TERMINATED)",
    "large_active": " AND AREA[EnrollmentCount]RANGE[3000,MAX]"
                    " AND AREA[OverallStatus](RECRUITING OR ACTIVE_NOT_RECRUITING)",
}


def fetch(advanced):
    studies, token = [], None
    while True:
        params = {"query.outc": OUTCOME_QUERY, "filter.advanced": advanced,
                  "fields": FIELDS, "pageSize": "200"}
        if token:
            params["pageToken"] = token
        with urllib.request.urlopen(f"{API}?{urllib.parse.urlencode(params)}", timeout=60) as r:
            page = json.load(r)
        studies += page.get("studies", [])
        token = page.get("nextPageToken")
        if not token:
            return studies


def flatten(study):
    p = study["protocolSection"]
    ident, status = p["identificationModule"], p["statusModule"]
    drugs = [i["name"] for i in p.get("armsInterventionsModule", {}).get("interventions", [])
             if i.get("type") in ("DRUG", "BIOLOGICAL")]
    return {
        "nct_id": ident["nctId"],
        "acronym": ident.get("acronym"),
        "title": ident.get("briefTitle"),
        "sponsor": p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name"),
        "drugs": drugs,
        "conditions": p.get("conditionsModule", {}).get("conditions", []),
        "enrollment": p.get("designModule", {}).get("enrollmentInfo", {}).get("count"),
        "status": status.get("overallStatus"),
        "why_stopped": status.get("whyStopped"),
        "start": status.get("startDateStruct", {}).get("date"),
        "primary_completion": status.get("primaryCompletionDateStruct", {}).get("date"),
        "has_results": study.get("hasResults"),
    }


def main():
    out = {name: [flatten(s) for s in fetch(PHASE3_DRUG + extra)] for name, extra in GROUPS.items()}
    path = pathlib.Path(__file__).resolve().parents[1] / "data" / "screening" / "cv_outcome_trials.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    for name, rows in out.items():
        print(f"{name}: {len(rows)}")


if __name__ == "__main__":
    main()
