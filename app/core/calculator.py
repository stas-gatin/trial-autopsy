"""Deterministic four-check calculator.

Given what a drug did to biomarkers and how big the outcome trial was, compute:
  1. the LDL-equivalent causal effect (choosing a trustworthy lipid measure),
  2. the expected hazard ratio at the trial's follow-up,
  3. the trial's power to detect that hazard ratio,
  4. a verdict (outcome + primary/secondary category + weakest link).

No model calls. Every number in the output can be traced to an input value or
to a constant in rules.py (which cites data/reference/coefficients.json).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist

from app.core import rules

_N = NormalDist()


def canonical(name: str | None) -> str:
    """Map free-text biomarker names to the calculator's vocabulary ("sPLA2-IIA mass" -> "sPLA2-IIA")."""
    n = (name or "").lower()
    rules_ = [("non-hdl", "non-HDL-C"), ("hdl", "HDL-C"), ("ldl", "LDL-C"), ("apob", "apoB"),
              ("apolipoprotein b", "apoB"), ("lp(a)", "Lp(a)"), ("lipoprotein(a)", "Lp(a)"),
              ("lipoprotein (a)", "Lp(a)"), ("lp-pla2", "Lp-PLA2 activity"), ("lppla2", "Lp-PLA2 activity"),
              ("spla2", "sPLA2-IIA"), ("crp", "hsCRP"), ("c-reactive", "hsCRP"), ("il-6", "IL-6"),
              ("interleukin-6", "IL-6"), ("il-1", "IL-1beta"), ("hba1c", "HbA1c"), ("glycated", "HbA1c"),
              ("weight", "body weight"), ("bmi", "body weight"), ("systolic", "SBP"), ("sbp", "SBP"),
              ("blood pressure", "SBP"), ("triglycer", "triglycerides")]
    for key, value in rules_:
        if key in n:
            return value
    return (name or "").strip()


@dataclass
class BiomarkerChange:
    biomarker: str
    abs_change: float | None = None   # placebo-adjusted, canonical unit (see rules)
    unit: str = ""
    pct_change: float | None = None   # placebo-adjusted percent
    baseline: float | None = None     # same unit as abs_change
    method: str = ""
    source: str = ""                  # source id(s); assay discordance is judged only within one source


@dataclass
class CaseInput:
    case_id: str
    mode: str                          # "autopsy" or "forecast"
    target_genetics: str               # "causal" | "not_causal" | "uncertain" | "unknown"
    intended_biomarker: str
    changes: list[BiomarkerChange]
    followup_years: float | None = None
    events: int | None = None          # actual total primary events (autopsy) or planned (forecast)
    alpha_two_sided: float = 0.05
    pathway: str | None = None         # e.g. "IL-1beta" for drugs outside the lipid/BP scale
    sbp_change_mmhg: float | None = None
    observed_hr: float | None = None
    observed_ci: tuple[float, float] | None = None


@dataclass
class Result:
    case_id: str
    ldl_equivalent_mmol: float | None
    basis: str
    time_factor: float | None
    expected_hr: float | None
    causal_rrr: float | None
    bp_factor: float
    power: float | None
    mde_hr_80: float | None
    outcome: str
    category: str | None
    secondary: list[str]
    weakest_link: str
    consistent_with_observed: bool | None
    assumptions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    partial_expected_hr: float | None = None   # indicative only, when the main mechanism is unquantified


def time_factor(years: float) -> float:
    """Fraction of the ~5-year CTT effect reached by a given median follow-up.

    CTT: RR 0.88 per mmol/L in year 1 vs 0.78 over ~5 years. We interpolate the
    log effect linearly from year 1 to year 5 and scale linearly below year 1.
    """
    year1 = math.log(rules.LDL_RR_PER_MMOL_YEAR1) / math.log(rules.LDL_RR_PER_MMOL_5Y)
    if years <= 1:
        return year1 * max(years, 0.0)
    if years >= 5:
        return 1.0
    return year1 + (1 - year1) * (years - 1) / 4


def power(expected_hr: float, events: int, alpha_two_sided: float = 0.05) -> float:
    """Schoenfeld approximation for a 1:1 trial (one-directional benefit)."""
    if expected_hr >= 1:
        return alpha_two_sided / 2
    z_alpha = _N.inv_cdf(1 - alpha_two_sided / 2)
    return _N.cdf(math.sqrt(events) / 2 * abs(math.log(expected_hr)) - z_alpha)


def minimal_detectable_hr(events: int, alpha_two_sided: float = 0.05, target_power: float = 0.8) -> float:
    z = _N.inv_cdf(1 - alpha_two_sided / 2) + _N.inv_cdf(target_power)
    return math.exp(-2 * z / math.sqrt(events))


def events_needed(expected_hr: float, alpha_two_sided: float = 0.05, target_power: float = 0.8) -> int | None:
    if expected_hr >= 1:
        return None
    z = _N.inv_cdf(1 - alpha_two_sided / 2) + _N.inv_cdf(target_power)
    return math.ceil(4 * z**2 / math.log(expected_hr) ** 2)


def _absolute(change: BiomarkerChange, assumptions: list[str]) -> float | None:
    """Absolute reduction as a positive number (canonical unit), or None."""
    if change.biomarker == "Lp(a)" and "nmol" in (change.unit or "").lower():
        # Lp(a) canonical unit is mg/dL; there is no fixed conversion (rules.LPA_NMOL_PER_MGDL is an assumption).
        assumptions.append(f"Lp(a) given in nmol/L, converted at {rules.LPA_NMOL_PER_MGDL} nmol/L per mg/dL")
        change = BiomarkerChange(change.biomarker, unit="mg/dL", pct_change=change.pct_change, method=change.method,
                                 source=change.source,
                                 abs_change=None if change.abs_change is None else change.abs_change / rules.LPA_NMOL_PER_MGDL,
                                 baseline=None if change.baseline is None else change.baseline / rules.LPA_NMOL_PER_MGDL)
    if change.abs_change is not None:
        return -change.abs_change
    if change.pct_change is None:
        return None
    baseline = change.baseline
    if baseline is None:
        baseline = rules.DEFAULT_BASELINE.get(change.biomarker)
        if baseline is None:
            return None
        assumptions.append(f"{change.biomarker}: no baseline given, assumed {baseline}")
    return -change.pct_change / 100 * baseline


def ldl_equivalent(changes: list[BiomarkerChange], assumptions: list[str]) -> tuple[float | None, str]:
    """LDL-equivalent reduction in mmol/L from the most trustworthy lipid measure, plus Lp(a)."""
    by = {canonical(c.biomarker): c for c in changes}
    ldl, nonhdl, apob, lpa = by.get("LDL-C"), by.get("non-HDL-C"), by.get("apoB"), by.get("Lp(a)")

    discordant = False
    same_source = not (ldl and apob and ldl.source and apob.source and ldl.source != apob.source)
    if ldl and apob and ldl.pct_change and apob.pct_change and same_source:
        ratio = abs(ldl.pct_change) / abs(apob.pct_change)
        discordant = ratio > rules.DISCORDANCE_RATIO or ratio < 1 / rules.DISCORDANCE_RATIO

    candidates = []
    if discordant:
        candidates = [("non-HDL-C", nonhdl, 1.0), ("apoB", apob, 1 / rules.APOB_MGDL_PER_LDL_MMOL)]
    else:
        candidates = [("LDL-C", ldl, 1.0), ("non-HDL-C", nonhdl, 1.0), ("apoB", apob, 1 / rules.APOB_MGDL_PER_LDL_MMOL)]

    lipid, basis = None, "no lipid change"
    for name, change, factor in candidates:
        if change is None:
            continue
        value = _absolute(change, assumptions)
        if value is not None:
            lipid, basis = value * factor, name + (" (LDL/apoB discordant)" if discordant else "")
            break

    total = lipid
    if lpa is not None:
        lpa_reduction = _absolute(lpa, assumptions)
        if lpa_reduction is not None:
            total = (total or 0.0) + lpa_reduction / rules.LPA_MGDL_PER_LDL_MMOL
            basis = basis + " + Lp(a)" if lipid is not None else "Lp(a)"
    return total, basis


def evaluate(case: CaseInput) -> Result:
    assumptions: list[str] = []
    notes: list[str] = []

    eq, basis = ldl_equivalent(case.changes, assumptions)
    years = case.followup_years
    if years is None:
        years = 5.0
        assumptions.append("follow-up unknown, effect shown at 5 years")
    g = time_factor(years)

    bp_factor = 1.0
    if case.sbp_change_mmhg:
        bp_factor = rules.SBP_RR_PER_10MMHG ** (-case.sbp_change_mmhg / 10 * g)
        if bp_factor > 1:
            notes.append(f"systolic BP +{case.sbp_change_mmhg} mmHg adds HR x{bp_factor:.3f}")

    # Size the drug only if its MAIN mechanism is something the calculator can quantify (or a
    # biomarker genetics proves non-causal). "No LDL change" does not mean "no benefit": a GLP-1,
    # SGLT2 or anti-inflammatory drug would otherwise get a confident false "failure".
    intended = canonical(case.intended_biomarker)
    unquantified_reason = None
    if case.pathway in rules.UNQUANTIFIED_CAUSAL_PATHWAYS:
        unquantified_reason = f"{case.pathway} pathway"
    elif intended and intended not in rules.QUANTIFIED_BIOMARKERS and intended not in rules.NON_CAUSAL_BIOMARKERS:
        unquantified_reason = f"main mechanism via {intended}"
    quantifiable = unquantified_reason is None
    lipid_hr = rules.LDL_RR_PER_MMOL_5Y ** (g * (eq or 0.0))
    expected_hr = lipid_hr * bp_factor if quantifiable else None
    causal_rrr = 1 - lipid_hr if quantifiable else None
    partial_hr = None
    if not quantifiable:
        notes.append(f"{unquantified_reason} has no validated trial-scale coefficient; effect size not computable")
        hba1c = next((c for c in case.changes if canonical(c.biomarker) == "HbA1c" and c.abs_change is not None), None)
        hba1c_factor = rules.HBA1C_RR_PER_POINT_UNCERTAIN ** (-hba1c.abs_change * g) if hba1c else 1.0
        partial_hr = lipid_hr * bp_factor * hba1c_factor
        if partial_hr < 0.995:
            notes.append(f"indicative only: measured lipid/BP{'/HbA1c (uncertain causal)' if hba1c else ''} changes "
                         f"alone would give HR {partial_hr:.3f}; the main mechanism is not included")

    pw = power(expected_hr, case.events, case.alpha_two_sided) if expected_hr and case.events else None
    mde = minimal_detectable_hr(case.events, case.alpha_two_sided) if case.events else None
    if case.events is None and expected_hr and expected_hr < 1:
        notes.append(f"events needed for 80% power: {events_needed(expected_hr, case.alpha_two_sided)}")

    outcome, category, secondary = _verdict(case, expected_hr, causal_rrr, pw, notes)
    consistent = None
    if case.observed_ci and expected_hr:
        consistent = case.observed_ci[0] <= expected_hr <= case.observed_ci[1]

    return Result(
        case_id=case.case_id,
        ldl_equivalent_mmol=round(eq, 3) if eq is not None else None,
        basis=basis,
        time_factor=round(g, 3),
        expected_hr=round(expected_hr, 3) if expected_hr else None,
        causal_rrr=round(causal_rrr, 3) if causal_rrr is not None else None,
        bp_factor=round(bp_factor, 3),
        power=round(pw, 3) if pw is not None else None,
        mde_hr_80=round(mde, 3) if mde else None,
        outcome=outcome,
        category=category,
        secondary=secondary,
        weakest_link=_weakest_link(case, causal_rrr, pw),
        consistent_with_observed=consistent,
        assumptions=assumptions,
        notes=notes,
        partial_expected_hr=round(partial_hr, 3) if partial_hr is not None else None,
    )


def _verdict(case, expected_hr, causal_rrr, pw, notes):
    intended_non_causal = canonical(case.intended_biomarker) in rules.NON_CAUSAL_BIOMARKERS
    secondary: list[str] = []

    if expected_hr is None:
        return ("not_computable", "insufficient_data", secondary)

    # Harm beyond anything the measured biomarkers explain.
    if case.observed_ci and case.observed_ci[0] > 1 and expected_hr < case.observed_ci[0]:
        return ("failure", "off_target_harm", secondary)
    if expected_hr > 1:
        secondary.append("off_target_harm")

    if causal_rrr < rules.NEGLIGIBLE_RRR:
        if case.target_genetics == "not_causal":
            category = "target_not_causal"
        elif intended_non_causal:
            category = "biomarker_not_causal"
        else:
            category = "effect_too_small"
        outcome = "failure" if case.mode == "autopsy" else "likely_failure"
        return (outcome, category, secondary)

    if pw is None:
        return ("not_computable", "insufficient_data", secondary)

    if intended_non_causal:
        secondary.append("biomarker_not_causal")
    if case.mode == "forecast":
        if pw >= rules.LIKELY_SUCCESS_POWER:
            outcome = "likely_success"
        elif pw >= rules.BORDERLINE_POWER:
            outcome = "borderline"
        else:
            outcome = "likely_failure"
    else:
        outcome = "success" if pw >= 0.5 else "failure"

    if outcome in ("success", "likely_success", "borderline") and pw >= 0.5:
        robust = causal_rrr >= rules.ROBUST_SUCCESS_RRR and pw >= rules.ROBUST_SUCCESS_POWER
        return (outcome, "robust_success" if robust else "narrow_success", secondary)
    return (outcome, "effect_too_small", secondary)


def _weakest_link(case, causal_rrr, pw) -> str:
    if case.target_genetics in ("not_causal", "uncertain", "unknown"):
        return f"target genetics: {case.target_genetics}"
    if canonical(case.intended_biomarker) in rules.NON_CAUSAL_BIOMARKERS:
        return f"intended biomarker {case.intended_biomarker} is not causal"
    if causal_rrr is None:
        return f"main mechanism ({canonical(case.intended_biomarker) or case.pathway}) has no validated effect-size conversion"
    if pw is None:
        return "trial size (events) unknown"
    if causal_rrr < rules.ROBUST_SUCCESS_RRR:
        return f"expected effect only {causal_rrr:.0%} relative risk reduction"
    return f"power {pw:.0%}"
