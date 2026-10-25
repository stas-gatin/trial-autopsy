"""Open Targets Platform GraphQL: drug mechanism, target genetics, competitor programs."""
from __future__ import annotations

from app.tools import http

API = "https://api.platform.opentargets.org/api/v4/graphql"
CORONARY = "MONDO_0005010"  # coronary artery disorder (EFO_0001645 no longer resolves)
GENETIC_SOURCES = ["gwas_credible_sets", "gene_burden", "eva", "orphanet", "gene2phenotype", "uniprot_variants",
                   "clingen", "genomics_england"]


def _q(query: str, variables: dict, tracer=None) -> dict:
    body = http.post_json(API, {"query": query, "variables": variables}, tracer=tracer, source="Open Targets")
    if body.get("errors"):
        raise http.SourceError("Open Targets: " + body["errors"][0].get("message", "")[:200])
    return body["data"]


def find_drug(name: str, tracer=None) -> dict | None:
    data = _q("""query($q:String!){search(queryString:$q, entityNames:["drug"], page:{index:0,size:3}){
                hits{id name}}}""", {"q": name}, tracer)
    hits = data["search"]["hits"]
    return hits[0] if hits else None


def drug_profile(chembl_id: str, tracer=None) -> dict:
    data = _q("""query($id:String!){drug(chemblId:$id){id name drugType maximumClinicalStage description
                synonyms{label} tradeNames{label}
                mechanismsOfAction{rows{mechanismOfAction actionType targets{id approvedSymbol approvedName}}}}}""",
              {"id": chembl_id}, tracer)
    return data["drug"] or {}


def target_genetics(ensembl_id: str, disease_id: str = CORONARY, tracer=None) -> dict:
    """Genetic association of a target with coronary disease, with top credible-set evidence."""
    data = _q("""query($e:String!,$d:String!,$s:[String!]){
        target(ensemblId:$e){approvedSymbol associatedDiseases(Bs:[$d]){rows{score datatypeScores{id score}}}}
        disease(efoId:$d){name evidences(ensemblIds:[$e], size:8, datasourceIds:$s){count rows{datasourceId score
          credibleSet{studyId beta pValueMantissa pValueExponent variant{rsIds} study{traitFromSource nSamples publicationDate pubmedId}}}}}}""",
              {"e": ensembl_id, "d": disease_id, "s": GENETIC_SOURCES}, tracer)
    target, disease = data.get("target") or {}, data.get("disease") or {}
    rows = (target.get("associatedDiseases") or {}).get("rows") or []
    scores = {x["id"]: round(x["score"], 3) for x in rows[0]["datatypeScores"]} if rows else {}
    evidence = []
    for row in ((disease.get("evidences") or {}).get("rows") or []):
        cs = row.get("credibleSet") or {}
        study = cs.get("study") or {}
        evidence.append({
            "datasource": row.get("datasourceId"), "score": round(row.get("score") or 0, 3),
            "study_id": cs.get("studyId"), "trait": study.get("traitFromSource"), "n": study.get("nSamples"),
            "publication_date": study.get("publicationDate"), "pmid": study.get("pubmedId"),
            "variant": ((cs.get("variant") or {}).get("rsIds") or [None])[0], "beta": cs.get("beta"),
            "p": f"{cs.get('pValueMantissa')}e{cs.get('pValueExponent')}" if cs.get("pValueMantissa") else None,
        })
    return {
        "target": target.get("approvedSymbol"), "disease": disease.get("name"), "disease_id": disease_id,
        "overall_score": round(rows[0]["score"], 3) if rows else 0.0,
        "genetic_association_score": scores.get("genetic_association", 0.0),
        "evidence_count": (disease.get("evidences") or {}).get("count", 0),
        "top_evidence": evidence,
    }


def target_programs(ensembl_id: str, tracer=None) -> list[dict]:
    data = _q("""query($e:String!){target(ensemblId:$e){approvedSymbol drugAndClinicalCandidates{rows{
                maxClinicalStage drug{id name drugType} diseases{disease{id name}}}}}}""", {"e": ensembl_id}, tracer)
    rows = ((data.get("target") or {}).get("drugAndClinicalCandidates") or {}).get("rows") or []
    return [{"drug": r["drug"]["name"], "chembl_id": r["drug"]["id"], "type": r["drug"]["drugType"],
             "stage": r.get("maxClinicalStage"),
             "diseases": sorted({d["disease"]["name"] for d in r.get("diseases") or [] if d.get("disease")})}
            for r in rows if r.get("drug")]
