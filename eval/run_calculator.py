"""Run the deterministic calculator on the golden set and compare with the answer key.

Usage: python3 -m eval.run_calculator   (from the project root)
Writes eval/results/calculator_<date>.json and prints a table.
"""
import json
import pathlib
from dataclasses import asdict
from datetime import date

from app.core.calculator import BiomarkerChange, CaseInput, evaluate

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "golden_set"


def load_inputs():
    raw = json.loads((ROOT / "eval" / "calc_inputs.json").read_text())
    for c in raw["cases"]:
        changes = [BiomarkerChange(**ch) for ch in c["changes"]]
        ci = tuple(c["observed_ci"]) if c.get("observed_ci") else None
        fields = {k: v for k, v in c.items() if k not in ("changes", "observed_ci", "input_notes")}
        yield CaseInput(changes=changes, observed_ci=ci, **fields)


def answer_key(case_id):
    path = GOLDEN / f"{case_id.replace('_72pct', '')}.json"
    data = json.loads(path.read_text())
    ce = data.get("consensus_explanation", {})
    return data["meta"].get("outcome_label"), ce.get("category"), ce.get("secondary_categories", [])


def main():
    rows = []
    for case in load_inputs():
        result = evaluate(case)
        label, category, secondary = answer_key(case.case_id)
        actual_success = label == "success"
        predicted_success = result.outcome in ("success", "likely_success")
        outcome_match = None if label in ("pending",) or result.outcome == "not_computable" or case.case_id.startswith("lpa") \
            else (predicted_success == actual_success)
        category_match = None if category in (None, "insufficient_data") else result.category == category
        any_match = None if category_match is None else (
            {result.category, *result.secondary} & {category, *secondary} != set())
        rows.append({**asdict(result), "answer_outcome": label, "answer_category": category,
                     "answer_secondary": secondary, "outcome_match": outcome_match,
                     "category_match": category_match, "any_label_match": any_match})

    out = ROOT / "eval" / "results"
    out.mkdir(exist_ok=True)
    (out / f"calculator_{date.today().isoformat()}.json").write_text(json.dumps(rows, indent=2))

    head = f"{'case':20}{'basis':32}{'eqLDL':>6}{'expHR':>7}{'power':>7}  {'predicted':28} {'answer key':28} out cat any"
    print(head)
    print("-" * len(head))
    mark = {True: "ok", False: "XX", None: " -"}
    for r in rows:
        pred = f"{r['outcome']}/{r['category']}"
        key = f"{r['answer_outcome']}/{r['answer_category']}"
        print(f"{r['case_id']:20}{r['basis'][:31]:32}{str(r['ldl_equivalent_mmol']):>6}{str(r['expected_hr']):>7}"
              f"{str(r['power']):>7}  {pred[:28]:28} {key[:28]:28} {mark[r['outcome_match']]:>3} {mark[r['category_match']]:>3} {mark[r['any_label_match']]:>3}")
    scored = [r for r in rows if r["outcome_match"] is not None]
    cats = [r for r in rows if r["category_match"] is not None]
    print(f"\noutcome correct: {sum(r['outcome_match'] for r in scored)}/{len(scored)}"
          f" | primary category correct: {sum(r['category_match'] for r in cats)}/{len(cats)}"
          f" | any label overlap: {sum(r['any_label_match'] for r in cats)}/{len(cats)}")


if __name__ == "__main__":
    main()
