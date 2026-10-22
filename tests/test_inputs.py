import unittest

from app.agents.analysis import check_inputs, complete_inputs
from app.agents.case_builder import change_direction


class DirectionFromQuote(unittest.TestCase):
    def test_nearest_direction_word_decides(self):
        q = ("Niacin use significantly increased HDL-C by 29% and decreased triglycerides by 23% and "
             "low-density lipoprotein cholesterol (LDL-C) by 8%")
        self.assertEqual(change_direction(q, 8), -1)
        self.assertEqual(change_direction(q, 29), 1)

    def test_explicit_sign_and_no_direction(self):
        self.assertEqual(change_direction("LDL-C −59% vs placebo", 59), -1)
        self.assertEqual(change_direction("increases (+19% to +24%)", 24), 1)
        self.assertEqual(change_direction("LDL-C was 71 mg/dL", 71), 0)


class CompleteInputs(unittest.TestCase):
    def facts(self):
        return {
            "f1": {"fact_id": "f1", "key": "planned_events", "value": 993, "unit": "events", "attrs": {}, "quote": "993 events"},
            "f2": {"fact_id": "f2", "key": "inclusion_threshold", "value": 70, "unit": "mg/dL", "attrs": {"biomarker": "Lp(a)"},
                   "quote": "Lp(a) >= 70 mg/dL"},
            "f3": {"fact_id": "f3", "key": "biomarker_change", "value": -8, "unit": "%", "attrs": {"biomarker": "LDL-C"},
                   "quote": "decreased LDL-C by 8%"},
        }

    def test_events_and_lpa_baseline_come_from_cited_facts(self):
        m = {"events": None, "events_fact_ids": ["f1"],
             "changes": [{"biomarker": "Lp(a)", "pct_change": -73.7, "baseline": None, "fact_ids": []}]}
        filled = complete_inputs(m, self.facts())
        self.assertEqual(m["events"], 993)
        self.assertEqual(m["changes"][0]["baseline"], 70.0)
        self.assertIn("f2", m["changes"][0]["fact_ids"])
        self.assertEqual(len(filled), 2)

    def test_mg_dl_ldl_is_converted_and_sign_follows_the_fact(self):
        m = {"events": 850, "changes": [{"biomarker": "LDL-C", "pct_change": 8.0, "baseline": 71, "unit": "mg/dL",
                                         "fact_ids": ["f3"]}]}
        complete_inputs(m, self.facts())
        c = m["changes"][0]
        self.assertEqual(c["unit"], "mmol/L")
        self.assertAlmostEqual(c["baseline"], 1.836, places=3)
        self.assertEqual(c["pct_change"], -8.0)

    def test_implausible_ldl_change_is_flagged(self):
        issues = check_inputs({"changes": [{"biomarker": "LDL-C", "abs_change": -5.7, "fact_ids": []}]}, {})
        self.assertTrue(any("implausibly large" in i for i in issues))


if __name__ == "__main__":
    unittest.main()
