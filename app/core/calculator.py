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
import re
from dataclasses import dataclass, field
from statistics import NormalDist

from app.core import rules

_N = NormalDist()


_CANON = [  # first match wins; whole-word patterns on a lower-cased name with spaces and hyphens collapsed
    (r"\bnon ?hdl\b", "non-HDL-C"), (r"\bvldl\b", "VLDL-C"), (r"\bldl ?p\b|\bldl\b.*\bparticle", "LDL-P"),
    (r"\bhdl\b", "HDL-C"), (r"\bldl\b", "LDL-C"), (r"\bapo ?b\b|\bapolipoprotein b\b", "apoB"),
    (r"\bs ?pla2\b|\bsecretory phospholipase", "sPLA2-IIA"),
    (r"\blp ?pla2\b|\blppla2\b|\blipoprotein associated phospholipase", "Lp-PLA2 activity"),
    (r"\blp ?\(a\)|\blipoprotein ?\(a\)|\blpa\b", "Lp(a)"),
    (r"\b(hs ?)?crp\b|\bc reactive", "hsCRP"), (r"\bil ?6\b|\binterleukin ?6\b", "IL-6"),
    (r"\bil ?1(?: ?(?:beta|b|\u03b2))?\b|\binterleukin ?1(?: ?(?:beta|\u03b2))?\b", "IL-1beta"),
    (r"\bhba1c\b|\bglycated\b|\ba1c\b", "HbA1c"), (r"\bweight\b|\bbmi\b", "body weight"),
    (r"\bdiastolic\b|\bdbp\b", "DBP"), (r"\bsystolic\b|\bsbp\b|\bblood pressure\b", "SBP"),
    (r"\btriglycer|\btg\b", "triglycerides"),
]


def canonical(name) -> str:
    """Map free-text biomarker names to the calculator's vocabulary ("sPLA2-IIA mass" -> "sPLA2-IIA",
    "non HDL cholesterol" -> "non-HDL-C", "Apo B" -> "apoB"). Unknown names come back unchanged."""
    if isinstance(name, (list, tuple)):
        name = next((x for x in name if isinstance(x, str)), "")
    raw = name if isinstance(name, str) else ""
    n = re.sub(r"[\s_\-]+", " ", raw.lower()).strip()
    n = re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", n))
    for pattern, value in _CANON:
        if re.search(pattern, n):
            return value
    return raw.strip()


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


def _to_canonical_unit(change: BiomarkerChange, assumptions: list[str]) -> BiomarkerChange:
    """The calculator works in mmol/L for LDL-C and non-HDL-C and in mg/dL for apoB and Lp(a). A value in another
    unit is converted here, so a unit slip upstream cannot change the size of the effect by a factor of 39 or 100."""
    name, unit = canonical(change.biomarker), (change.unit or "").lower().replace(" ", "")
    factor, new_unit = None, None
    if name in ("LDL-C", "non-HDL-C") and "mg/dl" in unit:
        factor, new_unit = 1 / rules.MGDL_PER_MMOL_CHOLESTEROL, "mmol/L"
    elif name == "apoB" and ("g/l" in unit and "mg/l" not in unit):
        factor, new_unit = 100.0, "mg/dL"
    elif name == "Lp(a)" and "nmol" in unit:
        # Lp(a) canonical unit is mg/dL; there is no fixed conversion (rules.LPA_NMOL_PER_MGDL is an assumption).
        factor, new_unit = 1 / rules.LPA_NMOL_PER_MGDL, "mg/dL"
        assumptions.append(f"Lp(a) given in nmol/L, converted at {rules.LPA_NMOL_PER_MGDL} nmol/L per mg/dL")
    baseline = change.baseline if change.baseline is not None and change.baseline > 0 else None
    if factor is None:
        return BiomarkerChange(name, abs_change=change.abs_change, unit=change.unit, pct_change=change.pct_change,
                               baseline=baseline, method=change.method, source=change.source)
    return BiomarkerChange(name, unit=new_unit, pct_change=change.pct_change, method=change.method, source=change.source,
                           abs_change=None if change.abs_change is None else change.abs_change * factor,
                           baseline=None if baseline is None else baseline * factor)


def _absolute(change: BiomarkerChange, assumptions: list[str]) -> float | None:
    """Absolute reduction as a positive number (canonical unit), or None."""
    change = _to_canonical_unit(change, assumptions)
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


def _percent(change: BiomarkerChange | None) -> float | None:
    """Signed percent change, given or derived from the absolute change and the baseline."""
    if change is None:
        return None
    if change.pct_change is not None:
        return change.pct_change
    if change.abs_change is not None and change.baseline:
        return change.abs_change / change.baseline * 100
    return None


def _sources(change: BiomarkerChange) -> set[str]:
    return {s.strip() for s in (change.source or "").split(",") if s.strip()}


def ldl_equivalent(changes: list[BiomarkerChange], assumptions: list[str],
                   intended: str | None = None) -> tuple[float | None, str]:
    """LDL-equivalent reduction in mmol/L from the most trustworthy lipid measure, plus Lp(a)."""
    by: dict[str, BiomarkerChange] = {}
    for c in changes:  # the first row for a biomarker is the analyst's primary one
        by.setdefault(canonical(c.biomarker), c)
    ldl, nonhdl, apob, lpa = by.get("LDL-C"), by.get("non-HDL-C"), by.get("apoB"), by.get("Lp(a)")

    # LDL-C and apoB disagree (a CETP inhibitor's assay artefact, or opposite directions): trust non-HDL-C/apoB.
    discordant = False
    pl, pa = _percent(ldl), _percent(apob)
    same_source = not (ldl and apob and _sources(ldl) and _sources(apob) and not (_sources(ldl) & _sources(apob)))
    if pl is not None and pa is not None and same_source and abs(pl - pa) >= rules.DISCORDANCE_MIN_POINTS:
        if (pl < 0) != (pa < 0) or min(abs(pl), abs(pa)) == 0:
            discordant = True
        else:
            ratio = abs(pl) / abs(pa)
            discordant = ratio > rules.DISCORDANCE_RATIO or ratio < 1 / rules.DISCORDANCE_RATIO
    # A triglyceride-lowering drug acts on TG-rich particles that LDL-C does not count; non-HDL-C and apoB do.
    tg_drug = canonical(intended) == "triglycerides"

    if discordant or tg_drug:
        candidates = [("non-HDL-C", nonhdl, 1.0), ("apoB", apob, 1 / rules.APOB_MGDL_PER_LDL_MMOL)]
    else:
        candidates = [("LDL-C", ldl, 1.0), ("non-HDL-C", nonhdl, 1.0), ("apoB", apob, 1 / rules.APOB_MGDL_PER_LDL_MMOL)]
    note = " (LDL/apoB discordant)" if discordant else (" (triglyceride-lowering drug)" if tg_drug else "")

    lipid, basis = None, "no lipid change"
    for name, change, factor in candidates:
        if change is None:
            continue
        value = _absolute(change, assumptions)
        if value is not None:
            lipid, basis = value * factor, name + note
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

    eq, basis = ldl_equivalent(case.changes, assumptions, case.intended_biomarker)
    years = case.followup_years
    if years is None or years <= 0:
        years = 5.0
        assumptions.append("follow-up unknown, effect shown at 5 years")
    g = time_factor(years)

    # Systolic BP: the dedicated field, or else an SBP row among the biomarker changes (never both).
    sbp = case.sbp_change_mmhg
    if sbp is None:
        row = next((c for c in case.changes if canonical(c.biomarker) == "SBP" and c.abs_change is not None), None)
        sbp = row.abs_change if row else None
    bp_factor = 1.0
    if sbp:
        bp_factor = rules.SBP_RR_PER_10MMHG ** (-sbp / 10 * g)
        if bp_factor > 1:
            notes.append(f"systolic BP +{sbp} mmHg adds HR x{bp_factor:.3f}")

    # Size the drug only if its MAIN mechanism is something the calculator can quantify (or a
    # biomarker genetics proves non-causal). "No LDL change" does not mean "no benefit": a GLP-1,
    # SGLT2 or anti-inflammatory drug would otherwise get a confident false "failure".
    intended = canonical(case.intended_biomarker)
    unquantified_reason = None
    pathway = canonical(case.pathway) if case.pathway else None
    if pathway in rules.UNQUANTIFIED_CAUSAL_PATHWAYS or (pathway or "").lower() in rules.UNQUANTIFIED_CAUSAL_PATHWAYS:
        unquantified_reason = f"{case.pathway} pathway"
    elif intended and intended not in rules.QUANTIFIED_BIOMARKERS and intended not in rules.NON_CAUSAL_BIOMARKERS:
        unquantified_reason = f"main mechanism via {intended}"
    # A sized mechanism with no measured value anywhere in the facts is unknown, not zero: sizing it as "no change"
    # would turn missing data into a confident "failure". A target proven non-causal fails whatever the size.
    # The main mechanism must itself be measured: an Lp(a) drug with only a small LDL-C change, or a triglyceride
    # drug with only LDL-C, is not "sized" by that other number. No intended biomarker named: any sized value counts.
    def measured(name):
        if name == "SBP":
            return sbp is not None
        row = next((c for c in case.changes if canonical(c.biomarker) == name), None)
        return row is not None and _absolute(row, []) is not None
    needs = rules.MEASURED_BY.get(intended or "")
    not_measured = (unquantified_reason is None and needs is not None and case.target_genetics != "not_causal"
                    and not any(measured(n) for n in needs))
    if not_measured:
        basis = f"{intended or 'main mechanism'} change not measured"
        notes.append(f"no measured change in {intended or 'any sized biomarker'}: the effect is unknown, not zero")
    quantifiable = unquantified_reason is None and not not_measured
    lipid_hr = rules.LDL_RR_PER_MMOL_5Y ** (g * (eq or 0.0))
    expected_hr = lipid_hr * bp_factor if quantifiable else None
    # The causal effect is everything the sized mechanisms predict: lipids and blood pressure together. A drug or a
    # strategy that only lowers blood pressure (SPRINT) would otherwise score no effect at all.
    causal_rrr = 1 - lipid_hr * bp_factor if quantifiable else None
    partial_hr = None
    if unquantified_reason:
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
        weakest_link=basis if not_measured else _weakest_link(case, causal_rrr, pw),
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
