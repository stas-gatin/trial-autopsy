"""Intake agent: resolve the trial and drug, choose the mode, check scope. Deterministic."""
from __future__ import annotations

import re
from datetime import date

from app.agents.state import OutOfScope, RunState
from app.tools import archive, ctgov, opentargets
from app.tools.documents import Document

AGENT = "Intake"
CARDIOMETABOLIC = re.compile(
    r"cardio|coronary|myocard|heart|atheroscl|vascular|stroke|lipid|cholesterol|lipoprotein|dyslip|"
    r"hypertrigly|diabet|acute coronary|\bACS\b|\bMACE\b|\bCVD\b|\bASCVD\b|hypertension|kidney", re.I)
PLACEBO = re.compile(r"placebo|standard of care|usual care|matching|vehicle", re.I)


def run(state: RunState):
    req = state.request
    if req.get("archive_case"):
        _load_archive(state, req["archive_case"])
    else:
        _load_live(state, req)

    _resolve_drug(state)
    _check_scope(state)
    _choose_mode(state)

    state.tracer.log(AGENT, "classify_case",
                     input_summary=f"trial={state.trial.get('nct_id')} as_of={state.as_of}",
                     output_summary=f"mode={state.mode}; drug={state.drug.get('name')}; "
                                    f"targets={[t['symbol'] for t in state.drug.get('targets', [])]}; "
                                    f"documents={len(state.docs)}",
                     target_agent="CaseBuilder")


def _load_archive(state: RunState, case_id: str):
    manifest = archive.manifest(case_id)
    state.case_id = case_id
    state.backtest = True
    state.as_of, state.docs = archive.load(case_id)
    requested = state.request.get("mode")
    state.mode = requested if requested in ("autopsy", "forecast") else manifest.get("mode", "autopsy")
    nct = manifest["nct_id"]
    registry = next((d for d in state.docs if d.type == "registry"), None)
    state.trial = dict((registry.meta or {}).get("summary", {})) if registry else {}
    state.trial.setdefault("nct_id", nct)
    # The registry has no acronym for some trials (AURORA, JUPITER): take the archive's short name, never a null.
    if not state.trial.get("acronym"):
        state.trial["acronym"] = (manifest.get("acronym") or "").split(" (")[0].strip() or None
    state.trial.setdefault("title", manifest.get("title"))
    state.trial["readout_date"] = manifest.get("readout_date")
    state.request.setdefault("drug", manifest.get("drug"))
    state.tracer.log(AGENT, "load_archive", input_summary=case_id,
                     output_summary=f"frozen archive as_of {state.as_of}: {len(state.docs)} documents; internet off")


def _load_live(state: RunState, req: dict):
    nct = (req.get("trial") or "").strip().upper()
    as_of = req.get("as_of")
    state.as_of = date.fromisoformat(as_of) if as_of else None
    if not re.fullmatch(r"NCT\d{8}", nct):
        raise OutOfScope(f"'{nct}' is not a ClinicalTrials.gov NCT number")
    state.study = ctgov.get_study(nct, tracer=state.tracer)
    state.trial = ctgov.summarize(state.study)
    state.case_id = req.get("case_id") or (state.trial.get("acronym") or nct).lower().replace(" ", "_")
    registry = ctgov.to_document(state.study, state.as_of)
    state.docs.append(registry)
    if req.get("readout_text"):
        state.docs.append(Document(
            source_id="user:readout", type="user_input", title="Readout text supplied by the user",
            text=req["readout_text"], pub_date=req.get("readout_date") or (state.as_of or date.today()).isoformat(),
            access="user"))
    state.tracer.log(AGENT, "fetch_registry", input_summary=nct,
                     output_summary=f"{state.trial.get('acronym')} | status {state.trial.get('status')} | "
                                    f"N {state.trial.get('enrollment')} | results posted {state.trial.get('has_results')}")


# Synonyms that only name a salt or chemical form ("... free acid", "... sodium") match papers about other products.
SALT_FORM = re.compile(r"\b(free acid|free base|sodium|calcium|potassium|magnesium|hydrochloride|mesylate|besylate|"
                       r"hydrate|anhydrous)\b", re.I)


def _resolve_drug(state: RunState):
    names = []
    if state.request.get("drug"):
        names.append(state.request["drug"])
    for i in state.trial.get("interventions", []):
        if i.get("type") in ("DRUG", "BIOLOGICAL") and not PLACEBO.search(i.get("name") or ""):
            names.append(i["name"])
            names += i.get("other_names") or []
    cleaned = [re.sub(r"\b(\d+(\.\d+)?\s?(mg|g|mcg))\b.*", "", name, flags=re.I).strip() for name in names]
    # Second pass, only if no name resolves as written: drop trademark signs and split "Brand (generic name)",
    # e.g. "Epanova® (omega-3 carboxylic acids)" -> "Epanova", "omega-3 carboxylic acids".
    variants = []
    for name in cleaned:
        plain = re.sub(r"[®™©]", "", name).strip()
        variants += [plain, *[x.strip() for x in re.split(r"[()]", plain) if x.strip()]]
    variants = [v for v in dict.fromkeys(variants) if v not in cleaned]
    for clean in cleaned + variants:
        try:
            hit = opentargets.find_drug(clean, tracer=state.tracer)
        except Exception as exc:  # source outage is reported, not fatal
            state.warnings.append(f"Open Targets drug lookup failed for {clean}: {exc}")
            continue
        if hit:
            profile = opentargets.drug_profile(hit["id"], tracer=state.tracer)
            mechs = (profile.get("mechanismsOfAction") or {}).get("rows") or []
            targets = {t["id"]: {"id": t["id"], "symbol": t["approvedSymbol"], "name": t.get("approvedName")}
                       for m in mechs for t in m.get("targets") or []}
            aliases = sorted({x["label"] for x in (profile.get("synonyms") or []) + (profile.get("tradeNames") or [])
                              if x.get("label") and x["label"].lower() != profile.get("name", "").lower()
                              and not SALT_FORM.search(x["label"])})
            state.drug = {"name": profile.get("name", hit["name"]).lower(), "chembl_id": hit["id"], "aliases": aliases,
                          "drug_type": profile.get("drugType"),
                          "mechanisms": sorted({m["mechanismOfAction"] for m in mechs}),
                          "targets": list(targets.values()), "searched_as": clean}
            return
    raise OutOfScope("could not resolve the investigational drug to a molecule with a known target")


def _check_scope(state: RunState):
    text = " ".join([state.trial.get("title") or "", " ".join(state.trial.get("conditions") or []),
                     " ".join(state.trial.get("primary_outcomes") or [])])
    if not CARDIOMETABOLIC.search(text):
        raise OutOfScope("Trial Autopsy covers phase 3 cardiovascular outcome trials only; this trial's conditions and "
                         "endpoints are not cardiovascular or metabolic")
    if not state.drug.get("targets"):
        state.warnings.append("no molecular target in Open Targets: the genetics check is skipped and reported as unknown")
        state.tracer.log(AGENT, "scope_note", output_summary="drug has no annotated molecular target; "
                         "continuing with biomarker evidence only", status="warning")


def _choose_mode(state: RunState):
    requested = state.request.get("mode") or "auto"
    if requested in ("autopsy", "forecast"):
        state.mode = requested
        return
    if state.backtest:
        return
    done = state.trial.get("status") in ("COMPLETED", "TERMINATED")
    has_readout = bool(state.request.get("readout_text")) or state.trial.get("has_results") \
        or bool(state.trial.get("why_stopped"))
    state.mode = "autopsy" if (done and has_readout) or state.request.get("readout_text") else "forecast"
