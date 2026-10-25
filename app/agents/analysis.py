"""Mechanism Analyst, Calculator step, Investigator, Critic and Reporter agents."""
from __future__ import annotations

import json
import re
from dataclasses import asdict

from app.agents import prompts, verifier
from app.agents.state import RunState
from app.core.calculator import BiomarkerChange, CaseInput, canonical, evaluate
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
    cleaned = normalise_mechanism(data)
    if cleaned:
        state.tracer.log("MechanismAnalyst", "normalise_output", output_summary="; ".join(cleaned)[:700],
                         status="warning", target_agent="Calculator", deterministic=True)
    if state.mechanism and state.critiques:
        kept = keep_verified_inputs(state.mechanism, data, state.critiques[-1].get("issues") or [], facts)
        if kept:
            state.tracer.log("MechanismAnalyst", "keep_verified_inputs", output_summary="; ".join(kept)[:700],
                             status="warning", target_agent="Calculator", deterministic=True)
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


_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _number_in(x):
    """A number from a model value: 12, "12", "-1.2 mmol/L", "1,612", "−0.5". Returns (number, rest of text)."""
    if isinstance(x, bool) or x is None:
        return None, ""
    if isinstance(x, (int, float)):
        return float(x), ""
    if not isinstance(x, str):
        return None, ""
    text = x.replace("\u2212", "-").replace("\u2013", "-").strip()
    m = _NUMBER.search(text)
    if not m:
        return None, ""
    try:
        return float(m.group().replace(",", "")), text[m.end():].strip()
    except ValueError:
        return None, ""


def _id_list(x) -> list:
    if isinstance(x, str):
        return [x] if x.strip() else []
    return [i for i in x if isinstance(i, str)] if isinstance(x, (list, tuple)) else []


def normalise_mechanism(m: dict) -> list[str]:
    """One pass over the analyst's answer before any code uses it: biomarker names to the calculator's vocabulary,
    numbers written as text to numbers, fact ids to lists, apoB in g/L to mg/dL, a reversed or textual confidence
    interval to an ordered pair, and impossible values (a zero or negative baseline or follow-up) to unknown.
    Returns a note per change made; well-formed output is returned untouched."""
    notes = [f"left out {len(d)} biomarker change(s) that were not objects" for d in [_keep_change_objects(m)] if d]
    empty = [c for c in m.get("changes") or [] if not canonical(c.get("biomarker"))
             and all(c.get(k) in (None, "") for k in ("abs_change", "pct_change"))]
    if empty:
        m["changes"] = [c for c in m["changes"] if c not in empty]
        notes.append(f"left out {len(empty)} biomarker change(s) with no name and no value")
    for c in m.get("changes") or []:
        raw = c.get("biomarker")
        name = canonical(raw)
        if isinstance(raw, str) and name and name != raw.strip():
            c["biomarker"], c["reported_as"] = name, raw
            notes.append(f"'{raw}' read as {name}")
        for k in ("abs_change", "pct_change", "baseline"):
            v = c.get(k)
            if isinstance(v, str) or isinstance(v, bool):
                n, rest = _number_in(v)
                c[k] = n
                if k != "pct_change" and rest and not c.get("unit") and n is not None:
                    c["unit"] = rest.split()[0]
                notes.append(f"{name} {k} '{v}' read as {n}")
        if isinstance(c.get("baseline"), (int, float)) and c["baseline"] <= 0:
            notes.append(f"{name} baseline {c['baseline']} is impossible: treated as unknown")
            c["baseline"] = None
        if "fact_ids" in c and not isinstance(c["fact_ids"], list):
            c["fact_ids"] = _id_list(c["fact_ids"])
        unit = (c.get("unit") or "").lower().replace(" ", "")
        if name == "apoB" and "g/l" in unit and "mg/l" not in unit:
            for k in ("abs_change", "baseline"):
                if isinstance(c.get(k), (int, float)):
                    c[k] = round(c[k] * 100, 3)
            c["unit"] = "mg/dL"
            notes.append("apoB converted from g/L to mg/dL (x100)")
    for k in ("events", "followup_years", "observed_hr", "alpha_two_sided", "sbp_change_mmhg"):
        v = m.get(k)
        if isinstance(v, str) or isinstance(v, bool):
            m[k], _ = _number_in(v)
            notes.append(f"{k} '{v}' read as {m[k]}")
    if isinstance(m.get("events"), float):
        m["events"] = int(round(m["events"]))
    if isinstance(m.get("followup_years"), (int, float)) and m["followup_years"] <= 0:
        notes.append(f"follow-up {m['followup_years']} years is impossible: treated as unknown")
        m["followup_years"] = None
    ci = m.get("observed_ci")
    if ci is not None:
        pair = [_number_in(x)[0] for x in ci] if isinstance(ci, (list, tuple)) else []
        fixed = sorted(pair) if len(pair) == 2 and None not in pair else None
        if fixed != ci:
            notes.append(f"observed CI {ci} read as {fixed}")
        m["observed_ci"] = fixed
    intended = m.get("intended_biomarker")
    if intended is not None and not isinstance(intended, str):
        m["intended_biomarker"] = canonical(intended)
        notes.append(f"intended biomarker {intended} read as {m['intended_biomarker']}")
    elif isinstance(intended, str) and intended.strip() and canonical(intended) != intended.strip():
        m["intended_biomarker"] = canonical(intended)
        notes.append(f"intended biomarker '{intended}' read as {m['intended_biomarker']}")
    for k in [k for k in m if k.endswith("fact_ids") and not isinstance(m[k], list)]:
        m[k] = _id_list(m[k])
    return notes


_MENTIONS = {"LDL-C": ("ldl",), "non-HDL-C": ("non-hdl", "non hdl"), "apoB": ("apob", "apo b", "apolipoprotein b"),
             "Lp(a)": ("lp(a)", "lipoprotein(a)", "lipoprotein (a)"), "HDL-C": ("hdl",), "triglycerides": ("triglycer",),
             "SBP": ("blood pressure", "sbp")}


def _has_value(c: dict) -> bool:
    return any(isinstance(c.get(k), (int, float)) and not isinstance(c.get(k), bool) for k in ("abs_change", "pct_change"))


def keep_verified_inputs(previous: dict, current: dict, issues: list, facts: dict) -> list[str]:
    """A refinement round must not lose what the previous round had verified. A biomarker change that had a value
    and cited verified facts, and that the critic did not question, is restored if the revision dropped it or left
    it empty; so are the event count and the follow-up. Code does this, not the prompt. Returns what was restored."""
    asked = " ".join(f"{i.get('issue', '')} {i.get('request', '')}" for i in issues if isinstance(i, dict)).lower()

    def questioned(name: str) -> bool:
        return any(t in asked for t in _MENTIONS.get(name, (name.lower(),)))

    def verified(ids) -> bool:
        return bool(ids) and all(i in facts for i in ids)

    restored = []
    now = {canonical(c.get("biomarker")): c for c in current.get("changes") or []}
    for old in previous.get("changes") or []:
        name = canonical(old.get("biomarker"))
        if not name or not _has_value(old) or not verified(old.get("fact_ids")) or questioned(name):
            continue
        new = now.get(name)
        if new is None or not _has_value(new):
            current["changes"] = [c for c in current.get("changes") or [] if c is not new] + [dict(old)]
            restored.append(f"{name} change restored from the previous round (verified, not questioned by the critic)")
    for key, ids, words in (("events", "events_fact_ids", ("event",)), ("followup_years", "followup_fact_ids", ("follow",))):
        if previous.get(key) is not None and current.get(key) is None and verified(previous.get(ids)) \
                and not any(w in asked for w in words):
            current[key], current[ids] = previous[key], previous.get(ids)
            restored.append(f"{key} {previous[key]} restored from the previous round")
    return restored


def normalise_issues(data: dict) -> list[dict]:
    """The critic's issues as objects; a bare string becomes a medium-severity issue for no particular agent."""
    issues = data.get("issues")
    if isinstance(issues, dict):
        issues = [issues]
    if not isinstance(issues, list):
        issues = []
    data["issues"] = [i if isinstance(i, dict) else {"severity": "medium", "issue": str(i)} for i in issues]
    return data["issues"]


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
    # An entry threshold ("LDL-C >= 70 mg/dL") is not the patients' starting level, which is well above it: a lipid
    # baseline that is just a cited threshold is left out, and the calculator applies its stated default instead.
    for c in m.get("changes") or []:
        bm, base = c.get("biomarker"), _num(c.get("baseline"))
        if bm not in ("LDL-C", "non-HDL-C", "apoB") or base is None:
            continue
        for i in c.get("fact_ids") or []:
            f = facts.get(i)
            v = _num(f["value"]) if f and f.get("key") == "inclusion_threshold" else None
            if v is None:
                continue
            levels = [v] if bm == "apoB" else [v, v / MGDL_PER_MMOL]
            if any(abs(base - x) <= 0.03 * abs(x) for x in levels if x):
                pct, absolute = _num(c.get("pct_change")), _num(c.get("abs_change"))
                c["baseline"] = None
                if pct is not None and absolute is not None and abs(absolute - pct / 100 * base) <= 0.03 * abs(absolute):
                    c["abs_change"] = None   # it was only the percent applied to the threshold
                filled.append(f"{bm} baseline {base} is the outcome trial's entry threshold ({i}), not the patients' "
                              f"starting level: left out, the calculator applies its stated default baseline")
                break
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
        a, pct, base = _num(c.get("abs_change")), _num(c.get("pct_change")), _num(c.get("baseline"))
        if a is not None and pct is not None and base and abs(a - pct / 100 * base) > 0.25 * max(abs(a), abs(pct / 100 * base)):
            issues.append(f"{c.get('biomarker')} abs_change {a} and pct_change {pct}% of baseline {base} disagree "
                          f"(the calculator uses abs_change)")
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

def _outcome_line(state: RunState) -> str:
    if state.mode != "autopsy":
        return "not known yet (forecast)"
    actual, basis = actual_outcome(state)
    if not actual:
        return "not stated in the evidence"
    return f"{'met its primary endpoint' if actual == 'success' else 'did not meet its primary endpoint'} ({basis})"


def investigate(state: RunState):
    facts_text, _ = facts_block(state)
    prompt = prompts.INVESTIGATOR_PROMPT.format(outcome=_outcome_line(state),
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
    normalise_issues(data)
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


# What a readout says, strongest first: an explicit statement about the primary endpoint; a trial stopped early for
# benefit or for harm; a trial stopped or abandoned without benefit; a significant reduction in events. "Stopped" or
# "discontinued" alone is not a failure ("stopped early for efficacy", "discontinuation rates were similar"), and a
# biomarker that fell significantly is not a result.
_TRIAL = r"(?:trial|study|program|programme|development|outcomes? study)"
_STOP = r"\b(?:stop\w*|halt\w*|clos\w*|end(?:s|ed)?|terminat\w*|discontinu\w*)"
_EVENTS = r"(?:events?|risk|mortality|deaths?|heart attacks?|strokes?|mace|endpoint|morbidity)"
_PATIENTS_REACHED = re.compile(r"\b(patients|participants|subjects)\b[^.]{0,80}\b(met|achieved|reached)\b", re.I)
_NEGATED = re.compile(r"\b(no|not|without|lack of|failed to)\b[^.]{0,40}\b(benefit|reduc\w*|decreas\w*|lower\w*|efficacy)", re.I)
READOUT_RULES = [
    ("failure", 0, re.compile(r"\b(did not|does not|didn't|failed to|fails? to) (meet|achieve|reach) (its|the|their) (\w+ ){0,2}primary|"
                              r"primary (\w+ ){0,3}(was |were )?not (met|achieved|reached)|missed (its|the) (\w+ ){0,2}primary|"
                              r"no (statistically )?significant (difference|reduction|benefit) in (the )?(\w+ ){0,2}primary", re.I)),
    ("failure", 0, re.compile(r"primary (composite |combined )?end[- ]?point(?:[^.]|\.\d){0,160}\b(was |were |is )?not (statistically )?significant|"
                              r"\bnot statistically significant[^.]{0,60}\bprimary", re.I)),
    ("success", 0, re.compile(r"\b(met|meets|achieved|achieves) (its|the|their|both) (\w+ ){0,2}primary|"
                              r"primary (\w+ ){0,3}(was|were) (met|achieved)", re.I)),
    ("stopped_harm", 1, re.compile(rf"{_STOP}[^.]{{0,120}}\b(imbalance|excess|increase\w*|higher)\b[^.]{{0,40}}\b(mortality|deaths?|"
                                   rf"cardiovascular events|harm)|{_STOP}[^.]{{0,80}}\b(safety (concerns?|reasons?)|harm)\b", re.I)),
    ("success", 1, re.compile(rf"{_STOP}\b[^.]{{0,40}}\bearly\b[^.]{{0,120}}\b(efficacy|benefit\w*|positive|reduc\w*|superior\w*|"
                              rf"overwhelming)", re.I)),
    ("failure", 2, re.compile(rf"futility|lack of efficacy|insufficient efficacy|unlikely to (show|demonstrate|meet|achieve|be met|be achieved)|"
                              rf"did not (benefit|show a (significant )?benefit)|\bno (significant )?(clinical )?benefit|"
                              rf"\bdid not (affect|improve|change) (\w+ ){{0,2}}(clinical )?outcomes|"
                              rf"\bno differences? (\w+ ){{0,4}}(with respect to|in|on) (the )?(\w+ ){{0,2}}primary|"
                              rf"\b(did|do|does) not (\w+ ){{0,2}}(reduce|lower|decrease) (\w+ ){{0,6}}{_EVENTS}|"
                              rf"{_STOP} (the |its |a |all )?([\w()\-\"'\u201c\u201d]+ ){{0,6}}{_TRIAL}|"
                              rf"{_TRIAL} (was |is |has been |will be )?(stopped|halted|terminated|discontinued)", re.I)),
    ("primary_p", 1, re.compile(r"primary (composite |combined )?end[- ]?point(?:[^.]|\.\d){0,160}\bp ?(=|<|<=|≤) ?0?\.\d+|"
                                r"\bp ?(=|<|<=|≤) ?0?\.\d+(?:[^.]|\.\d){0,80}\bprimary (composite |combined )?end[- ]?point", re.I)),
    ("success", 3, re.compile(rf"(?<!no )(?<!not )(?<!non-)\bstatistically significant (\w+ ){{0,2}}(difference|reduction)(?:[^.]|\.\d){{0,40}}"
                              rf"\b(event rates?|{_EVENTS})", re.I)),
    ("success", 3, re.compile(rf"(?<![\w-])significant(ly)?\s+(\S+\s+){{0,3}}(reduc|decreas|lower)\w*\s+(\w+\s+){{0,6}}{_EVENTS}",
                              re.I)),
    ("failure", 4, re.compile(r"did not (\w+ ){0,2}(reduce|lower|decrease)|no (statistically )?significant (reduction|difference|benefit)|"
                              r"\b(non-?|in)significant (\w+ ){0,2}(reduction|difference|benefit|decrease)|"
                              r"too small to (show|detect|demonstrate)", re.I)),
]


def _primary_p(text: str) -> str | None:
    """A p-value stated with the primary endpoint: below 0.05 the endpoint was met, else not."""
    m = re.search(r"\bp ?(=|<|<=|≤) ?(0?\.\d+)", text, re.I)
    if not m:
        return None
    value = float(m.group(2))
    return "success" if value < 0.05 or (m.group(1) in ("<", "<=", "≤") and value <= 0.05) else "failure"


def classify_readout(text: str):
    """(outcome, rank) for the strongest statement in one sentence or title, or None."""
    text = text.replace("\u00b7", ".")   # 'p=0\u00b7943': the Lancet's raised decimal point
    for outcome, rank, rule in READOUT_RULES:
        if outcome == "primary_p":
            if rule.search(text) and _primary_p(text):
                return _primary_p(text), rank
            continue
        if rank == 0 and outcome == "success" and _PATIENTS_REACHED.search(text):
            continue
        if rule.search(text) and not (rank in (1, 3) and outcome == "success" and _NEGATED.search(text)):
            return outcome, rank
    return None


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
    # The outcome facts extracted for this trial first; only if they state nothing, the announcement itself (its
    # title, then each sentence), which may also speak of other trials of the same programme.
    readout_types = {"user_input", "press_release", "registry"}
    doc_types = {d.source_id: d.type for d in state.docs}
    facts = []
    for f in state.context.facts(state.case_id):
        if f["key"] not in ("readout_statement", "stopped_early", "primary_endpoint"):
            continue
        if doc_types.get(f["source_id"]) not in readout_types and not f["source_id"].startswith(("user:", "announcement:")):
            continue
        facts.append((str(f["value"]) + " " + f["quote"], f["source_id"]))
    texts = []
    for d in state.docs:
        if d.type in ("press_release", "user_input") or d.source_id.startswith(("announcement:", "user:")):
            texts.append((d.title or "", d.source_id))
            texts += [(x, d.source_id) for x in re.split(r"(?<=[.!?])\s+", d.text or "") if x.strip()]
    def strongest(candidates):
        best = None
        for text, source in candidates:
            hit = classify_readout(text)
            if hit and (best is None or hit[1] < best[0]):
                best = (hit[1], hit[0], f"readout: \"{text.strip()[:140]}\" ({source})")
        return best
    # An explicit statement about the primary endpoint in the announcement outranks a weaker fact.
    from_facts, from_text = strongest(facts), strongest(texts)
    best = from_facts
    if from_text and (best is None or (from_text[0] == 0 and best[0] > 0)):
        best = from_text
    return (best[1], best[2]) if best else (None, "outcome not stated in the evidence")


def verdict_block(state: RunState) -> dict:
    c, inv = state.calc, state.investigation or {}
    actual, basis = actual_outcome(state)
    primary = inv.get("primary_category") or c.category
    if actual in ("failure", "stopped_harm") and primary in SUCCESS_CODES:
        primary = "effect_too_small" if (c.power or 0) < 0.9 else "insufficient_data"
    elif actual == "success" and primary not in SUCCESS_CODES:
        # A trial that met its endpoint is not explained by a failure cause: take the investigator's own success
        # category if it named one, else "with caveats" (the benefit is real but the system could not explain it).
        primary = next((x for x in inv.get("secondary_categories") or [] if x in SUCCESS_CODES), "success_with_caveats")
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
