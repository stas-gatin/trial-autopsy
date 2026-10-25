"""3D structure and plain-language profile of a drug's target protein.

Choice of structure, in order:
  1. an experimental structure (RCSB PDB) that contains the drug itself, found by full-text search;
  2. the best experimental structure covering most of the protein (PDBe "best structures");
  3. the AlphaFold predicted model.
Function text comes from UniProt. All requests go through http.py (cached, offline-capable).
"""
from __future__ import annotations

import re

from app.tools import http

RCSB_SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"
PDBE_BEST = "https://www.ebi.ac.uk/pdbe/api/mappings/best_structures/{uniprot}"
PDBE_CHAINS = "https://www.ebi.ac.uk/pdbe/api/mappings/uniprot/{pdb}"
ALPHAFOLD = "https://alphafold.ebi.ac.uk/api/prediction/{uniprot}"
UNIPROT = "https://rest.uniprot.org/uniprotkb/{uniprot}?fields=protein_name,cc_function,length&format=json"
UNIPROT_FEATURES = "https://rest.uniprot.org/uniprotkb/{uniprot}?fields=ft_domain,ft_repeat,ft_propep&format=json"
OT = "https://api.platform.opentargets.org/api/v4/graphql"
FILES = {"pdb": "https://files.rcsb.org/download/{id}.pdb", "alphafold": "{url}"}


def uniprot_for(ensembl_id: str) -> str | None:
    body = http.post_json(OT, {"query": "query($e:String!){target(ensemblId:$e){proteinIds{id source}}}",
                               "variables": {"e": ensembl_id}}, source="Open Targets")
    ids = ((body.get("data") or {}).get("target") or {}).get("proteinIds") or []
    swiss = [p["id"] for p in ids if p.get("source") == "uniprot_swissprot"]
    return swiss[0] if swiss else None


def _function(uniprot: str) -> dict:
    d = http.get_json(UNIPROT.format(uniprot=uniprot), source="UniProt")
    name = (((d.get("proteinDescription") or {}).get("recommendedName") or {}).get("fullName") or {}).get("value")
    texts = [c["texts"][0]["value"] for c in d.get("comments", []) if c.get("commentType") == "FUNCTION" and c.get("texts")]
    function = re.sub(r"\s*\((PubMed|ECO):[^)]*\)", "", texts[0]) if texts else ""
    sentences = re.split(r"(?<=\.)\s+", function)
    return {"protein_name": name, "function": " ".join(sentences[:2]), "length": (d.get("sequence") or {}).get("length")}


def _features(uniprot: str) -> list[dict]:
    """Domains, repeats and the propeptide (UniProt residue numbering), for colouring the 3D model."""
    try:
        d = http.get_json(UNIPROT_FEATURES.format(uniprot=uniprot), source="UniProt")
    except (http.SourceError, http.OfflineMiss):
        return []
    out = []
    for f in d.get("features") or []:
        loc = f.get("location") or {}
        start, end = (loc.get("start") or {}).get("value"), (loc.get("end") or {}).get("value")
        if not (start and end):
            continue
        name = "Prodomain (propeptide)" if f.get("type") == "Propeptide" else (f.get("description") or f.get("type"))
        out.append({"name": name, "group": re.sub(r"\s+\d+$", "", name), "kind": f.get("type"), "start": start, "end": end})
    return out


def _drug_bound(uniprot: str, drug_names: list[str]) -> tuple[str, str] | None:
    for name in drug_names:
        query = {"query": {"type": "group", "logical_operator": "and", "nodes": [
            {"type": "terminal", "service": "full_text", "parameters": {"value": name}},
            {"type": "terminal", "service": "text", "parameters": {
                "attribute": "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_accession",
                "operator": "exact_match", "value": uniprot}}]},
            "return_type": "entry", "request_options": {"paginate": {"start": 0, "rows": 3}}}
        try:
            body = http.post_json(RCSB_SEARCH, query, source="RCSB PDB")
        except (http.SourceError, ValueError):
            continue
        hits = (body or {}).get("result_set") or []
        if hits:
            return hits[0]["identifier"].lower(), name
    return None


def _best_experimental(uniprot: str) -> dict | None:
    try:
        rows = http.get_json(PDBE_BEST.format(uniprot=uniprot), source="PDBe").get(uniprot) or []
    except http.SourceError:
        return None
    rows = [r for r in rows if (r.get("coverage") or 0) >= 0.5]
    rows.sort(key=lambda r: (-(r.get("coverage") or 0), r.get("resolution") or 99))
    return rows[0] if rows else None


def _target_chains(pdb_id: str, uniprot: str) -> list[str]:
    try:
        body = http.get_json(PDBE_CHAINS.format(pdb=pdb_id), source="PDBe")
    except http.SourceError:
        return []
    mappings = ((body.get(pdb_id) or {}).get("UniProt") or {}).get(uniprot, {}).get("mappings", [])
    return sorted({m.get("chain_id") for m in mappings if m.get("chain_id")})


def _chain_map(pdb_id: str, uniprot: str) -> dict:
    """Per target chain: the UniProt position of the first mapped residue and its sequential index in the chain.
    The UI combines this with the residue numbers in the PDB file to shift UniProt domain positions onto the model."""
    try:
        body = http.get_json(PDBE_CHAINS.format(pdb=pdb_id), source="PDBe")
    except http.SourceError:
        return {}
    out = {}
    for m in ((body.get(pdb_id) or {}).get("UniProt") or {}).get(uniprot, {}).get("mappings", []):
        c = m.get("chain_id")
        if c and c not in out:
            out[c] = {"unp_start": m.get("unp_start"), "seq_start": (m.get("start") or {}).get("residue_number")}
    return out


def target_profile(symbol: str, ensembl_id: str, drug_names: list[str]) -> dict:
    uniprot = uniprot_for(ensembl_id)
    if not uniprot:
        return {"symbol": symbol, "available": False, "reason": "no reviewed UniProt entry"}
    info = _function(uniprot)
    profile = {"symbol": symbol, "uniprot": uniprot, "available": True, **info, "features": _features(uniprot),
               "links": {"uniprot": f"https://www.uniprot.org/uniprotkb/{uniprot}/entry"}}

    bound = _drug_bound(uniprot, [n for n in drug_names if n])
    related = False
    if not bound:
        try:
            from app.tools import opentargets
            others = [p["drug"].lower() for p in opentargets.target_programs(ensembl_id)
                      if p["drug"].lower() not in {n.lower() for n in drug_names}][:5]
            bound = _drug_bound(uniprot, others)
            related = bool(bound)
        except (http.SourceError, http.OfflineMiss):
            bound = None
    if bound:
        pdb_id, drug = bound
        label = (f"Experimental structure {pdb_id.upper()} with {drug} bound"
                 + (" (another drug on the same target)" if related else ""))
        profile.update({"source": "pdb", "id": pdb_id, "label": label, "drug_bound": drug, "related_drug": related,
                        "chains": _target_chains(pdb_id, uniprot), "chain_map": _chain_map(pdb_id, uniprot)})
        profile["links"]["structure"] = f"https://www.rcsb.org/structure/{pdb_id.upper()}"
        return profile

    best = _best_experimental(uniprot)
    if best:
        res = f", {best['resolution']:.1f} Å" if best.get("resolution") else ""
        profile.update({"source": "pdb", "id": best["pdb_id"],
                        "label": f"Experimental structure {best['pdb_id'].upper()} ({best.get('experimental_method', '').lower()}{res})",
                        "chains": _target_chains(best["pdb_id"], uniprot) or ([best["chain_id"]] if best.get("chain_id") else []),
                        "chain_map": _chain_map(best["pdb_id"], uniprot)})
        profile["links"]["structure"] = f"https://www.rcsb.org/structure/{best['pdb_id'].upper()}"
        return profile

    try:
        af = http.get_json(ALPHAFOLD.format(uniprot=uniprot), source="AlphaFold")
        af = af[0] if isinstance(af, list) else af          # the API answers with a list; read through Zetaris, the entry
        profile.update({"source": "alphafold", "id": af["entryId"], "url": af["pdbUrl"],
                        "label": f"AlphaFold predicted model {af['entryId']}", "chains": []})
        profile["links"]["structure"] = f"https://alphafold.ebi.ac.uk/entry/{uniprot}"
    except (http.SourceError, IndexError, KeyError):
        profile.update({"source": None, "label": "no 3D structure available"})
    return profile


def structure_file(profile: dict) -> str | None:
    if profile.get("source") == "pdb":
        return http.get_text(FILES["pdb"].format(id=profile["id"].upper()), source="RCSB PDB")
    if profile.get("source") == "alphafold":
        return http.get_text(profile["url"], source="AlphaFold")
    return None
