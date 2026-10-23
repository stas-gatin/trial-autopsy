"""Mechanism Analyst, Calculator step, Investigator, Critic and Reporter agents."""
from __future__ import annotations

import json
import re
from dataclasses import asdict

from app.agents import prompts, verifier
from app.agents.state import RunState
from app.core.calculator import BiomarkerChange, CaseInput, evaluate
from app.llm import generator
from app.tools import reference

MGDL_PER_MMOL = 38.67


def facts_block(state: RunState) -> tuple[str, dict]:
    facts = state.context.build(state.case_id, for_agent="analysis")["facts"]
    lines = []
    for f in facts:
        attrs = json.dumps(f["attrs"], sort_keys=True, ensure_ascii=False) if f["attrs"] else ""
        lines.append(f"{f['fact_id']} | {f['key']} | {f['value']} {f['unit']} | {attrs} | "
                     f"{f['source']} ({f['source_date'] or 'n/a'})")
    return "\n".join(lines) or "(no verified facts)", {f["fact_id"]: f for f in facts}


def _genetics_block(state: RunState) -> str:
    rows = []
    for g in state.genetics:
        rows.append(f"- {g['target']}: genetic association score {g['genetic_association_score']} "
                    f"(overall {g['overall_score']}), {g['evidence_count']} genetic evidence items")
        for ev in g["top_evidence"][:5]:
            rows.append(f"    study {ev['study_id']} ({ev['trait']}, n={ev['n']}, published {ev['publication_date']}, "
                        f"before cut-off: {ev['published_before_cutoff']}) variant {ev['variant']} beta {ev['beta']}")
    return "\n".join(rows) or "(no genetic evidence found)"


def _reference_block() -> str:
    seen, rows = set(), []
    for e in reference.brief():
        if e["biomarker"] in seen:
            continue
        seen.add(e["biomarker"])
        rows.append(f"- {e['biomarker']}: causal={e['causal']}")
    return "\n".join(rows)


def calc_json(calc) -> str:
    """Calculator result for prompts. Optional fields are omitted when empty so prompts stay stable."""
    data = asdict(calc)
    if data.get("partial_expected_hr") is None:
        data.pop("partial_expected_hr", None)
    return json.dumps(data, default=str)


def _llm(state: RunState, role: str, system: str, prompt: str, tag: str, agent: str):
    data, response = generator.generate_json(role, system, prompt, tag=f"{state.case_id}:{tag}", agent=agent)
    state.record_model(agent, response)
    return data, response


# --- Mechanism Analyst --------------------------------------------------------

def _feedback(state: RunState, agent: str) -> str:
    """Critic issues addressed to this agent from the previous round (empty on the first pass)."""
    if not state.critiques:
        return ""
    issues = [i for i in state.critiques[-1].get("issues") or []
              if i.get("target_agent") == agent and i.get("severity") in ("high", "medium")]
    if not issues:
        return ""
    lines = "\n".join(f"- [{i.get('severity')}] {i.get('issue')} -> {i.get('request', '')}" for i in issues)
    return (f"\nCritic feedback on your previous answer (address every point; change inputs where the critic is right, "
            f"but never replace a quantified, verified input with null because a better source is not available: keep "
            f"the best verified estimate and state the caveat in method):\n{lines}\n")


def mechanism(state: RunState):
    facts_text, facts = facts_block(state)
    prompt = prompts.MECHANISM_PROMPT.format(
        trial_label=state.trial_label, mode=state.mode, as_of=state.as_of or "today",
        drug=state.drug.get("name"), mechanism="; ".join(state.drug.get("mechanisms", [])),
        targets=", ".join(t["symbol"] for t in state.drug.get("targets", [])),
        genetics=_genetics_block(state), reference=_reference_block(), facts=facts_text,
        feedback=_feedback(state, "mechanism"))
    data, response = _llm(state, "reasoning", prompts.MECHANISM_SYSTEM, prompt, "mechanism", "MechanismAnalyst")
    dropped = _keep_change_objects(data)
    if dropped:
        state.tracer.log("MechanismAnalyst", "drop_malformed", output_summary=f"{len(dropped)} biomarker change(s) "
                         f"were not objects and were left out: {dropped}"[:500], status="warning", deterministic=True)
    filled = complete_inputs(data, facts)
    state.mechanism = data
    state.case_input = to_case_input(state, data)
    state.auto_issues = check_inputs(data, facts)
    state.tracer.log("MechanismAnalyst", "derive_calculator_inputs",
                     input_summary=f"{len(facts)} verified facts, genetics for "
                                   f"{[g['target'] for g in state.genetics]}",
                     output_summary=f"target genetics {data.get('target_genetics')}; intended biomarker "
                                    f"{data.get('intended_biomarker')}; {len(data.get('changes') or [])} biomarker "
                                    f"changes; events {data.get('events')} ({data.get('events_basis')}); "
                                    f"missing: {data.get('missing')}",
                     target_agent="Calculator", model=f"{response.provider}:{response.model}", cached=response.cached)
    if filled:
        state.tracer.log("MechanismAnalyst", "code_completion", output_summary="; ".join(filled)[:700],
                         target_agent="Calculator", deterministic=True)
    if state.auto_issues:
        state.tracer.log("MechanismAnalyst", "self_check", output_summary="; ".join(state.auto_issues)[:700],
                         status="warning", target_agent="Critic")


def _keep_change_objects(m: dict) -> list:
    """The analyst must return each biomarker change as an object; anything else (a bare note string) is left out
    here, in code, so later steps never see it. Returns what was left out; well-formed output is not touched."""
    changes = m.get("changes")
    if changes is None:
        return []
    if isinstance(changes, dict):
        m["changes"] = [changes]
        return []
    if not isinstance(changes, list):
        m["changes"] = []
        return [changes]
    dropped = [c for c in changes if not isinstance(c, dict)]
    if dropped:
        m["changes"] = [c for c in changes if isinstance(c, dict)]
    return dropped


def _lpa_threshold(facts: dict) -> dict | None:
    """The outcome trial's Lp(a) entry threshold for its overall population (the lowest one stated)."""
    best = None
    for f in facts.values():
        if f["key"] not in ("inclusion_threshold", "baseline_level"):
            continue
        attrs = f.get("attrs") or {}
        text = f"{attrs.get('biomarker', '')} {f.get('quote', '')}".lower()
        if "lp(a)" not in text and "lipoprotein(a)" not in text and "lipoprotein (a)" not in text:
            continue
        if f["key"] == "baseline_level" and attrs.get("phase") != "phase3":
            continue
        v = _num(f["value"])
        unit = (f.get("unit") or "").lower()
        if v is None or not ("mg/dl" in unit or "nmol" in unit):
            continue
        if best is None or (f["key"] == "baseline_level") > (best["key"] == "baseline_level") or \
                (f["key"] == best["key"] and v < _num(best["value"])):
            best = f
    return best


def complete_inputs(m: dict, facts: dict) -> list[str]:
    """Fill numbers the analyst left empty although a verified fact it cited (or the outcome trial) states them.
    Code, not the model, does this, and every filled value names its fact. Returns what was filled."""
    filled = []
    for c in m.get("changes") or []:
        bm, unit = c.get("biomarker"), (c.get("unit") or "").lower()
        # LDL-C and non-HDL-C go to the calculator in mmol/L; a mg/dL value or baseline is converted here.
        if bm in ("LDL-C", "non-HDL-C"):
            base = _num(c.get("baseline"))
            if "mg/dl" in unit or (base is not None and base > 20):
                for k in ("abs_change", "baseline"):
                    v = _num(c.get(k))
                    if v is not None and (k == "abs_change" and "mg/dl" in unit or k == "baseline" and v > 20):
                        c[k] = round(v / MGDL_PER_MMOL, 3)
                c["unit"] = "mmol/L"
                filled.append(f"{bm} converted from mg/dL to mmol/L (/{MGDL_PER_MMOL})")
        # The direction of a change comes from the cited verified facts when they all agree.
        cited = [_num(facts[i]["value"]) for i in c.get("fact_ids") or []
                 if i in facts and facts[i].get("key") == "biomarker_change" and _num(facts[i]["value"])]
        if cited and (all(v < 0 for v in cited) or all(v > 0 for v in cited)):
            sign = -1 if cited[0] < 0 else 1
            for k in ("abs_change", "pct_change"):
                v = _num(c.get(k))
                if v and (v < 0) != (sign < 0):
                    c[k] = -v
                    filled.append(f"{bm} {k} sign set to match cited facts {c.get('fact_ids')}")
    if _num(m.get("events")) is None:
        cited = [facts[i] for i in m.get("events_fact_ids") or [] if i in facts and _num(facts[i]["value"]) is not None]
        if len(cited) == 1:
            f = cited[0]
            m["events"] = int(_num(f["value"]))
            m["events_basis"] = "actual" if f["key"] in ("events_total",) else "planned"
            filled.append(f"events {m['events']} ({m['events_basis']}) from cited fact {f['fact_id']}")
    for c in m.get("changes") or []:
        if c.get("biomarker") != "Lp(a)" or _num(c.get("abs_change")) is not None or _num(c.get("baseline")) is not None \
                or _num(c.get("pct_change")) is None:
            continue
        f = _lpa_threshold(facts)
        if f is None:
            continue
        c["baseline"] = _num(f["value"])
        unit = f.get("unit") or "mg/dL"
        c["unit"] = "nmol/L" if "nmol" in unit.lower() else "mg/dL"
        c["fact_ids"] = list(c.get("fact_ids") or []) + [f["fact_id"]]
        what = "measured phase 3 baseline" if f["key"] == "baseline_level" else \
            "outcome trial's entry threshold, a lower bound for the true baseline"
        c["method"] = (c.get("method") or "") + f"; baseline {c['baseline']} {c['unit']} = {what} ({f['fact_id']}), set by code"
        filled.append(f"Lp(a) baseline {c['baseline']} {c['unit']} from {f['fact_id']} ({what})")
    return filled


def to_case_input(state: RunState, m: dict) -> CaseInput:
    facts = {f["fact_id"]: f for f in state.context.build(state.case_id, for_agent="calculator_inputs")["facts"]}
    changes = []
    for c in m.get("changes") or []:
        sources = sorted({facts[f]["source"] for f in c.get("fact_ids") or [] if f in facts})
        changes.append(BiomarkerChange(biomarker=c.get("biomarker", ""), abs_change=_num(c.get("abs_change")),
                                       unit=c.get("unit", ""), pct_change=_num(c.get("pct_change")),
                                       baseline=_num(c.get("baseline")), method=c.get("method") or "",
                                       source=",".join(sources)))
    ci = m.get("observed_ci")
    return CaseInput(
        case_id=state.case_id, mode=state.mode, target_genetics=m.get("target_genetics") or "unknown",
        intended_biomarker=m.get("intended_biomarker") or "", changes=changes,
        followup_years=_num(m.get("followup_years")), events=int(m["events"]) if _num(m.get("events")) else None,
        alpha_two_sided=_num(m.get("alpha_two_sided")) or 0.05, pathway=m.get("pathway"),
        sbp_change_mmhg=_num(m.get("sbp_change_mmhg")),
        observed_hr=_num(m.get("observed_hr")) if state.mode == "autopsy" else None,
        observed_ci=tuple(ci) if state.mode == "autopsy" and isinstance(ci, list) and len(ci) == 2 else None)


def _num(x):
    try:
        return float(x) if x is not None and x != "" else None
    except (TypeError, ValueError):
        return None


def check_inputs(m: dict, facts: dict) -> list[str]:
    """Code-level checks: every number must trace to a cited, verified fact."""
    issues = []

    def values(ids):
        out = []
        for i in ids or []:
            if i not in facts:
                issues.append(f"cites unknown or unverified fact {i}")
                continue
            v = _num(facts[i]["value"])
            if v is not None:
                out.append(v)
        return out

    for c in m.get("changes") or []:
        cited = values(c.get("fact_ids"))
        for field_name in ("abs_change", "pct_change"):
            v = _num(c.get(field_name))
            if v is None:
                continue
            candidates = cited + [x / MGDL_PER_MMOL for x in cited] + [x * MGDL_PER_MMOL for x in cited]
            if not any(abs(abs(v) - abs(x)) <= max(0.02 * abs(x), 0.011) for x in candidates):
                issues.append(f"{c.get('biomarker')} {field_name}={v} does not match cited facts {c.get('fact_ids')}")
        if not c.get("fact_ids"):
            issues.append(f"{c.get('biomarker')} change has no fact citation")
    if _num(m.get("events")) is not None:
        cited = values(m.get("events_fact_ids"))
        if cited and not (any(abs(x - float(m["events"])) <= 1 for x in cited) or abs(sum(cited) - float(m["events"])) <= 2):
            issues.append(f"events={m['events']} does not match cited facts")
    for key in ("followup_fact_ids", "outcome_fact_ids"):
        values(m.get(key))
    for c in m.get("changes") or []:
        within = [i for i in c.get("fact_ids") or [] if i in facts and facts[i].get("key") == "biomarker_change"
                  and (facts[i].get("attrs") or {}).get("placebo_adjusted") is False]
        if within:
            issues.append(f"{c.get('biomarker')} change uses within-arm values {within} (not placebo-adjusted): "
                          f"use the difference from the comparator arm if the source gives one")
        if c.get("biomarker") in ("LDL-C", "non-HDL-C") and abs(_num(c.get("abs_change")) or 0) > 3:
            issues.append(f"{c.get('biomarker')} change {c.get('abs_change')} mmol/L is implausibly large (unit error?)")
    return issues


# --- Calculator ---------------------------------------------------------------

def calculate(state: RunState):
    state.calc = evaluate(state.case_input)
    c = state.calc
    state.tracer.log("Calculator", "four_checks",
                     input_summary=f"LDL-eq basis {c.basis}; follow-up {state.case_input.followup_years}y; "
                                   f"events {state.case_input.events}",
                     output_summary=f"expected HR {c.expected_hr}, causal RRR {c.causal_rrr}, power {c.power}, "
                                    f"MDE {c.mde_hr_80} -> {c.outcome}/{c.category} {c.secondary}",
                     target_agent="Investigator", deterministic=True)


# --- Investigator -------------------------------------------------------------

def investigate(state: RunState):
    facts_text, _ = facts_block(state)
    prompt = prompts.INVESTIGATOR_PROMPT.format(
        trial_label=state.trial_label, mode=state.mode, as_of=state.as_of or "today", drug=state.drug.get("name"),
        mechanism="; ".join(state.drug.get("mechanisms", [])),
        case_input=json.dumps(asdict(state.case_input), default=str),
        calc=calc_json(state.calc), facts=facts_text,
        feedback=_feedback(state, "investigator"))
    data, response = _llm(state, "reasoning", prompts.INVESTIGATOR_SYSTEM, prompt,
                          f"investigate:{len(state.critiques)}", "Investigator")
    state.investigation = data
    state.tracer.log("Investigator", "differential_diagnosis",
                     output_summary=f"primary {data.get('primary_category')} ({data.get('confidence')}); "
                                    f"secondary {data.get('secondary_categories')}; "
                                    f"{len(data.get('hypotheses') or [])} hypotheses weighed; departure from "
                                    f"calculator: {data.get('departure_from_calculator')}",
                     target_agent="Critic", model=f"{response.provider}:{response.model}", cached=response.cached)


# --- Critic -------------------------------------------------------------------

def critique(state: RunState) -> dict:
    facts_text, _ = facts_block(state)
    prompt = prompts.CRITIC_PROMPT.format(
        trial_label=state.trial_label, mode=state.mode,
        auto_issues="\n".join(f"- {i}" for i in state.auto_issues) or "- none",
        verification=verifier.summary(state),
        case_input=json.dumps(asdict(state.case_input), default=str),
        calc=calc_json(state.calc),
        hypotheses=json.dumps(state.investigation, ensure_ascii=False), facts=facts_text)
    data, response = _llm(state, "critic", prompts.CRITIC_SYSTEM, prompt, f"critic:{len(state.critiques)}", "Critic")
    state.critiques.append(data)
    high = [i for i in data.get("issues") or [] if i.get("severity") == "high"]
    state.tracer.log("Critic", "review", output_summary=f"{len(data.get('issues') or [])} issues ({len(high)} high); "
                                                         f"verdict_ok={data.get('verdict_ok')}: " +
                     "; ".join(i.get("issue", "") for i in high)[:500],
                     target_agent=high[0].get("target_agent") if high else "Reporter",
                     status="needs_revision" if high else "success",
                     model=f"{response.provider}:{response.model}", cached=response.cached)
    return data


# --- Reporter -----------------------------------------------------------------

def report(state: RunState):
    sources = [{"id": d.source_id, "title": d.title, "date": d.pub_date, "access": d.access} for d in state.docs]
    verdict = verdict_block(state)
    titles = {d.source_id: d.title for d in state.docs}
    fact_lines = "\n".join(f"{f['fact_id']} | {f['key']} | {f['value']} {f['unit']} | "
                           f"{titles.get(f['source_id'], f['source_id'])}"
                           for f in state.context.facts(state.case_id))
    prompt = prompts.REPORTER_PROMPT.format(facts=fact_lines,
        trial_label=state.trial_label, mode=state.mode, as_of=state.as_of or "today", drug=state.drug.get("name"),
        mechanism="; ".join(state.drug.get("mechanisms", [])), verdict=json.dumps(verdict),
        calc=calc_json(state.calc), hypotheses=json.dumps(state.investigation, ensure_ascii=False),
        readthrough=json.dumps(state.readthrough, default=str), sources=json.dumps(sources, ensure_ascii=False))
    data, response = _llm(state, "reporter", prompts.REPORTER_SYSTEM, prompt, "report", "Reporter")
    state.report = data
    state.tracer.log("Reporter", "final_synthesis", output_summary=data.get("headline", ""),
                     model=f"{response.provider}:{response.model}", cached=response.cached)


NOT_MET = re.compile(r"did not (meet|achieve)|not met|missed|fail(ed)? to|futility|lack of efficacy|"
                     r"insufficient efficacy|no (significant )?(reduction|benefit)|discontinu|stopped|halted|"
                     r"terminat|unlikely to (show|demonstrate)", re.I)
MET = re.compile(r"\bmet (its|the) primary|achieved (its|the) primary|significant(ly)? reduc", re.I)
SUCCESS_CODES = {"robust_success", "narrow_success", "success_with_caveats"}


def actual_outcome(state: RunState) -> tuple[str | None, str]:
    """What actually happened, read deterministically from the outcome facts (autopsy only)."""
    if state.mode != "autopsy":
        return None, ""
    ci = state.case_input.observed_ci if state.case_input else None
    if ci:
        if ci[1] < 1:
            return "success", f"observed HR CI {ci[0]}-{ci[1]} excludes 1"
        if ci[0] > 1:
            return "stopped_harm", f"observed HR CI {ci[0]}-{ci[1]} is above 1"
        return "failure", f"observed HR CI {ci[0]}-{ci[1]} includes 1"
    readout_types = {"user_input", "press_release", "registry"}
    doc_types = {d.source_id: d.type for d in state.docs}
    for f in state.context.facts(state.case_id):
        if f["key"] not in ("readout_statement", "stopped_early", "primary_endpoint"):
            continue
        if doc_types.get(f["source_id"]) not in readout_types and not f["source_id"].startswith(("user:", "announcement:")):
            continue
        text = str(f["value"]) + " " + f["quote"]
        if NOT_MET.search(text):
            return "failure", f"readout: \"{f['quote'][:140]}\" ({f['source_id']})"
        if MET.search(text):
            return "success", f"readout: \"{f['quote'][:140]}\" ({f['source_id']})"
    return None, "outcome not stated in the evidence"


def verdict_block(state: RunState) -> dict:
    c, inv = state.calc, state.investigation or {}
    actual, basis = actual_outcome(state)
    primary = inv.get("primary_category") or c.category
    if actual in ("failure", "stopped_harm") and primary in SUCCESS_CODES:
        primary = "effect_too_small" if (c.power or 0) < 0.9 else "insufficient_data"
    consistency = ""
    if actual and c.power is not None:
        expected_success = c.outcome in ("success", "likely_success")
        if (actual == "success") == expected_success:
            consistency = f"consistent with the calculator (probability of success {round(c.power * 100)}%)"
        else:
            consistency = (f"calculator expected {'success' if expected_success else 'failure'} with "
                           f"{round((c.power if expected_success else 1 - c.power) * 100)}% probability; "
                           f"the actual result falls in the remaining "
                           f"{round((1 - c.power if expected_success else c.power) * 100)}%")
    confidence = inv.get("confidence") or "medium"
    verification = (state.verification or {}).get("status")
    if verification == "unavailable":   # the numbers could not be checked independently (no Zetaris): one level lower
        confidence = {"high": "medium", "medium": "low"}.get(confidence, confidence)
    return {
        "outcome": actual or c.outcome,
        "outcome_basis": basis or "calculator forecast",
        "predicted_outcome": c.outcome,
        "consistency": consistency,
        "primary_category": primary,
        "secondary_categories": inv.get("secondary_categories") or c.secondary,
        "calculator_category": c.category,
        "confidence": confidence,
        "verification": verification or "not_run",
        "probability_of_success": c.power,
        "weakest_link": c.weakest_link,
    }
