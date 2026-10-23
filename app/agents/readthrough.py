"""Read-through agent: what the case means for other programmes on the same target.

For each competitor (Open Targets clinical candidates on the same target): find its
outcome trial (ClinicalTrials.gov), extract its phase 2 effect on the case's intended
biomarker from a published abstract, then run the calculator in forecast mode.
"""
from __future__ import annotations

from app.agents import prompts
from app.agents.state import RunState
from app.core.calculator import BiomarkerChange, CaseInput, evaluate
from app.llm import generator
from app.tools import ctgov, europepmc, opentargets
from app.tools.documents import verify_quote

AGENT = "ReadThrough"
MAX_PROGRAMS = 2
NMOL_PER_MGDL_LPA = 2.4  # assumption; there is no fixed Lp(a) unit conversion
ACTIVE = ("RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION")


def run(state: RunState):
    if state.mode != "autopsy" or state.backtest or not state.case_input:
        state.tracer.log(AGENT, "skip", output_summary="read-through runs only for live autopsies")
        return
    from app import config
    if not config.source_enabled("opentargets") or not config.source_enabled("europepmc"):
        state.tracer.log(AGENT, "skip", output_summary="read-through needs Open Targets and Europe PMC (switched off in Settings)",
                         status="warning")
        return
    intended = state.case_input.intended_biomarker
    programs = []
    for target in state.drug.get("targets", []):
        try:
            for p in opentargets.target_programs(target["id"], tracer=state.tracer):
                if p["chembl_id"] != state.drug.get("chembl_id") and p["stage"] in ("PHASE_3", "PHASE_2"):
                    programs.append({**p, "target": target["symbol"]})
        except Exception as exc:
            state.warnings.append(f"read-through lookup failed: {exc}")
    programs.sort(key=lambda p: (p["stage"] != "PHASE_3", p["drug"]))
    state.tracer.log(AGENT, "find_programs", output_summary=f"{len(programs)} other clinical programmes on "
                     f"{[t['symbol'] for t in state.drug.get('targets', [])]}: {[p['drug'] for p in programs]}")

    pending = []
    for program in programs[:MAX_PROGRAMS]:
        try:
            state.readthrough.append(_assess(state, program, intended))
        except generator.PendingModelCall as exc:
            pending.append(exc)
    if pending:
        raise generator.PendingBatch(pending)


def _assess(state: RunState, program: dict, intended: str) -> dict:
    drug = program["drug"].lower()
    entry = {"drug": drug, "target": program["target"], "stage": program["stage"], "trial": None,
             "biomarker_change": None, "expected_hr_5y": None, "events_needed_80": None, "comparison": "", "notes": []}
    trials = ctgov.search(query_intr=drug, advanced="AREA[Phase]PHASE3", tracer=state.tracer)
    outcome_trials = []
    for t in trials:
        s = ctgov.summarize(t)
        outcome_trials.append(s)
    cv = [s for s in outcome_trials if any(k in " ".join(s["primary_outcomes"] + [s["title"] or ""]).lower()
                                           for k in ("cardiovascular", "mace", "coronary", "myocardial", "events"))]
    pool = cv or outcome_trials
    active = [s for s in pool if s["status"] in ACTIVE] or pool
    if active:
        best = max(active, key=lambda s: s.get("enrollment") or 0)
        entry["trial"] = {k: best.get(k) for k in ("nct_id", "acronym", "status", "enrollment", "primary_completion")}

    records = europepmc.search(f'(TITLE:"{drug}" OR ABSTRACT:"{drug}") AND (PUB_TYPE:"Clinical Trial, Phase II" OR '
                               f'PUB_TYPE:"Randomized Controlled Trial" OR PUB_TYPE:"Clinical Trial")',
                               page_size=10, tracer=state.tracer)
    marker = intended.lower().replace("-c", "")
    records = [r for r in records if drug in (r["title"] + r["abstract"]).lower() and "%" in r["abstract"]
               and marker in r["abstract"].lower()]
    if not records:
        entry["notes"].append("no phase 2 abstract with a reported effect found")
        return entry
    changes, baselines, doc, response = [], [], None, None
    for record in records[:2]:
        doc = europepmc.to_document(record)
        prompt = prompts.EXTRACTION_PROMPT.format(trial_label=f"{drug} programme", drug=drug, doc_title=doc.title,
                                                  doc_type=doc.type, pub_date=doc.pub_date, source_id=doc.source_id,
                                                  text=doc.text)
        data, response = generator.generate_json("extraction", prompts.EXTRACTION_SYSTEM, prompt,
                                                 tag=f"{state.case_id}:readthrough:{drug}", agent="ReadThrough",
                                                 list_key="facts", retry=False)
        state.record_model("ReadThrough", response)
        facts = [f for f in data.get("facts", []) if (f.get("attrs") or {}).get("biomarker") == intended
                 and verify_quote(doc.text, f.get("quote", ""))]
        changes = [f for f in facts if f.get("key") == "biomarker_change"]
        baselines = [f for f in facts if f.get("key") == "baseline_level"]
        if changes:
            break
    if not changes:
        entry["notes"].append(f"no verified {intended} change in {doc.source_id}")
        return entry
    best = min(changes, key=lambda f: (not (f.get("attrs") or {}).get("placebo_adjusted", False), _num(f["value"]) or 0))
    pct = _num(best["value"]) if (best.get("attrs") or {}).get("kind", "percent") == "percent" else None
    baseline = _baseline(baselines, intended, state, entry)
    entry["biomarker_change"] = {"biomarker": intended, "value": best["value"], "unit": best.get("unit"),
                                 "quote": best.get("quote"), "source": doc.source_id, "source_title": doc.title}
    case = CaseInput(case_id=f"{state.case_id}:rt:{drug}", mode="forecast",
                     target_genetics=state.case_input.target_genetics, intended_biomarker=intended,
                     changes=[BiomarkerChange(intended, pct_change=pct, baseline=baseline)])
    result = evaluate(case)
    entry["expected_hr_5y"] = result.expected_hr
    entry["events_needed_80"] = next((int(n.split(":")[1]) for n in result.notes if n.startswith("events needed")), None)
    main = state.calc.expected_hr
    if result.expected_hr and main:
        entry["comparison"] = ("larger expected effect than the index drug" if result.expected_hr < main - 0.02 else
                               "similar expected effect to the index drug" if abs(result.expected_hr - main) <= 0.02
                               else "smaller expected effect than the index drug")
    entry["notes"] += result.assumptions
    state.tracer.log(AGENT, "forecast_competitor", input_summary=f"{drug} ({doc.source_id})",
                     output_summary=f"{intended} {best['value']}{best.get('unit') or ''} -> expected HR "
                                    f"{result.expected_hr} at 5y; {entry['comparison']}",
                     model=f"{response.provider}:{response.model}", cached=response.cached)
    return entry


def _baseline(baselines, intended, state, entry):
    for b in baselines:
        value, unit = _num(b.get("value")), (b.get("unit") or "").lower()
        if value is None:
            continue
        if intended == "Lp(a)" and "nmol" in unit:
            entry["notes"].append(f"baseline {value} nmol/L converted at {NMOL_PER_MGDL_LPA} nmol/L per mg/dL")
            return value / NMOL_PER_MGDL_LPA
        return value
    for c in state.case_input.changes:
        if c.biomarker == intended and c.baseline:
            entry["notes"].append(f"baseline borrowed from the index trial ({c.baseline})")
            return c.baseline
    return None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
