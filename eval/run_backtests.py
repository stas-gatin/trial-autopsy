"""Score the backtest runs (data/runs/bt-<case>.json) against the answer key.

Three questions per case:
  1. Extraction: did the agent feed the calculator the right numbers? Compared with the hand-built
     answer key (eval/calc_inputs.json) on the LDL-equivalent effect, events and follow-up.
  2. Prediction: did the calculator, using ONLY the agent's inputs from documents published before the
     readout, predict the right outcome (success vs failure)?
  3. Explanation: does the final primary category match the consensus explanation (data/golden_set)?

Usage: python -m eval.run_backtests [--runs DIR] [--label NAME]
  -> prints a table, writes eval/results/backtest_<date>[_<label>].json and .md
"""
import argparse
import json
import pathlib
from datetime import date

from app.core.calculator import BiomarkerChange, CaseInput, evaluate

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNS = ROOT / "data" / "runs"
GOLD = ROOT / "data" / "golden_set"


def answer_inputs() -> dict:
    raw = json.loads((ROOT / "eval" / "calc_inputs.json").read_text())["cases"]
    return {c["case_id"]: c for c in raw}


def key_result(c: dict):
    changes = [BiomarkerChange(**ch) for ch in c["changes"]]
    fields = {k: v for k, v in c.items() if k not in ("changes", "observed_ci", "input_notes")}
    return evaluate(CaseInput(changes=changes, observed_ci=None, **{**fields, "observed_hr": None}))


def rel_err(a, b):
    if a is None or b is None:
        return None
    if b == 0:
        return abs(a)
    return abs(a - b) / abs(b)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(RUNS), help="folder with bt-<case>.json runs")
    ap.add_argument("--label", default="", help="suffix for the result files, e.g. nemotron")
    args = ap.parse_args(argv)
    runs = pathlib.Path(args.runs)
    runs = runs if runs.is_absolute() else ROOT / runs
    suffix = f"_{args.label}" if args.label else ""
    key = answer_inputs()
    rows = []
    for run_file in sorted(runs.glob("bt-*.json")):
        case = run_file.stem[3:]
        run = json.loads(run_file.read_text())
        gold = json.loads((GOLD / f"{case}.json").read_text())
        actual = "success" if gold["meta"]["outcome_label"] == "success" else "failure"
        row = {"case": case, "status": run["status"], "actual": actual,
               "gold_category": gold["consensus_explanation"]["category"],
               "gold_secondary": gold["consensus_explanation"].get("secondary_categories", [])}
        if run["status"] != "success":
            row["error"] = (run.get("error") or {}).get("type")
            rows.append(row)
            continue
        out = run["output"]
        row["models"] = sorted(set((out.get("models_used") or {}).values()))
        calc, inputs, verdict = out["calculator"], out["calculator_inputs"], out["verdict"]
        predicted_success = calc["outcome"] in ("success", "likely_success")
        k = key.get(case)
        kres = key_result(k) if k else None
        row.update({
            "agent_ldl_eq": calc["ldl_equivalent_mmol"], "key_ldl_eq": kres.ldl_equivalent_mmol if kres else None,
            "agent_events": inputs["events"], "key_events": k and k.get("events"),
            "agent_followup": inputs["followup_years"], "key_followup": k and k.get("followup_years"),
            "agent_target_genetics": inputs["target_genetics"], "key_target_genetics": k and k.get("target_genetics"),
            "agent_expected_hr": calc["expected_hr"], "key_expected_hr": kres.expected_hr if kres else None,
            "agent_power": calc["power"], "predicted": "success" if predicted_success else "failure",
            "calc_outcome": calc["outcome"],
            "prediction_correct": None if calc["outcome"] == "not_computable" else predicted_success == (actual == "success"),
            "final_category": verdict["primary_category"], "final_secondary": verdict["secondary_categories"],
            "facts": len(out["facts"]), "sources": len(out["sources"]),
        })
        row["category_correct"] = row["final_category"] == row["gold_category"]
        row["category_overlap"] = bool({row["final_category"], *row["final_secondary"]} &
                                       {row["gold_category"], *row["gold_secondary"]})
        err = rel_err(row["agent_expected_hr"], row["key_expected_hr"])
        row["expected_hr_abs_diff"] = None if err is None else round(abs(row["agent_expected_hr"] - row["key_expected_hr"]), 3)
        rows.append(row)

    done = [r for r in rows if r["status"] == "success"]
    pred = [r for r in done if r["prediction_correct"] is not None]
    summary = {
        "date": date.today().isoformat(), "cases": len(rows), "completed": len(done),
        "prediction_correct": sum(r["prediction_correct"] for r in pred), "prediction_scored": len(pred),
        "category_correct": sum(r["category_correct"] for r in done),
        "category_overlap": sum(r["category_overlap"] for r in done),
        "expected_hr_within_0_03": sum(1 for r in done if r.get("expected_hr_abs_diff") is not None and r["expected_hr_abs_diff"] <= 0.03),
        "expected_hr_compared": sum(1 for r in done if r.get("expected_hr_abs_diff") is not None),
    }
    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"backtest_{summary['date']}{suffix}.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))

    lines = ["| case | actual | calculator (agent inputs) | power | exp. HR agent / key | final category | key category | pred | cat |",
             "|---|---|---|---|---|---|---|---|---|"]
    mark = {True: "✅", False: "❌", None: "–"}
    for r in rows:
        if r["status"] != "success":
            lines.append(f"| {r['case']} | {r['actual']} | {r['status']} ({r.get('error')}) | | | | {r['gold_category']} | | |")
            continue
        lines.append(f"| {r['case']} | {r['actual']} | {r['calc_outcome']} | {r['agent_power']} | "
                     f"{r['agent_expected_hr']} / {r['key_expected_hr']} | {r['final_category']} | {r['gold_category']} | "
                     f"{mark[r['prediction_correct']]} | {mark[r['category_correct']]}{'' if r['category_correct'] else (' (overlap)' if r['category_overlap'] else '')} |")
    lines.append("")
    lines.append(f"Prediction correct: {summary['prediction_correct']}/{summary['prediction_scored']} · "
                 f"primary category correct: {summary['category_correct']}/{summary['completed']} · "
                 f"category overlap: {summary['category_overlap']}/{summary['completed']} · "
                 f"expected HR within 0.03 of answer key: {summary['expected_hr_within_0_03']}/{summary['expected_hr_compared']}")
    text = "\n".join(lines)
    (out_dir / f"backtest_{summary['date']}{suffix}.md").write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
