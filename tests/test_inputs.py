import unittest

from app.agents.analysis import check_inputs, complete_inputs
from app.agents.case_builder import change_direction, sentence_before


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

    def test_direction_from_the_start_of_the_sentence(self):
        # the quote starts mid-sentence, after the verb that gives the direction
        doc = ("Results. Evolocumab reduced LDL-C by 66% (95% CI, 58% to 73%) to 75% (biweekly) and by 63% "
               "(95% CI, 54% to 71%) to 75% (monthly) vs placebo at the mean of weeks 10 and 12. Adverse events were similar.")
        quote = "and by 63% (95% CI, 54% to 71%) to 75% (monthly) vs placebo"
        self.assertEqual(change_direction(quote, 63), 0)
        before = sentence_before(doc, quote)
        self.assertTrue(before.startswith("evolocumab reduced"))
        self.assertEqual(change_direction(f"{before} {quote}", 63, window=480), -1)
        self.assertEqual(sentence_before(doc, "not in the document at all"), "")

    def test_sentence_is_used_only_when_the_quote_says_nothing(self):
        from app.agents.case_builder import _number_in
        # a level change written as two levels: the difference is not in the quote, so the sentence is not read
        self.assertFalse(_number_in("lowered the triglyceride level from 164 mg per deciliter to 122 mg per deciliter", 42))
        self.assertTrue(_number_in("and by 63% (95% CI, 54% to 71%) vs placebo", 63))
        self.assertFalse(_number_in("a 138.2 mg/dL level", 38))


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


class ResolveDrugName(unittest.TestCase):
    """A registry name like 'Epanova® (omega-3 carboxylic acids)' still resolves; names as written are tried first."""

    def _resolve(self, request_drug, interventions, known):
        from types import SimpleNamespace
        from unittest import mock
        from app.agents import intake
        tried = []

        def find(name, tracer=None):
            tried.append(name)
            return {"id": known[name], "name": name} if name in known else None

        profile = {"name": "drug", "mechanismsOfAction": {"rows": []}, "synonyms": [], "tradeNames": []}
        state = SimpleNamespace(request={"drug": request_drug}, trial={"interventions": interventions},
                                tracer=None, warnings=[], drug=None)
        with mock.patch.object(intake.opentargets, "find_drug", side_effect=find), \
                mock.patch.object(intake.opentargets, "drug_profile", return_value=profile):
            intake._resolve_drug(state)
        return state.drug, tried

    def test_brand_name_with_trademark_and_generic_in_brackets(self):
        drug, tried = self._resolve("omega-3 carboxylic acids",
                                    [{"type": "DRUG", "name": "Epanova® (omega-3 carboxylic acids)"},
                                     {"type": "DRUG", "name": "corn oil control"}],
                                    {"Epanova": "CHEMBL3137327"})
        self.assertEqual(drug["chembl_id"], "CHEMBL3137327")
        self.assertEqual(tried[:3], ["omega-3 carboxylic acids", "Epanova® (omega-3 carboxylic acids)", "corn oil control"])

    def test_a_name_that_resolves_as_written_wins_over_variants(self):
        drug, tried = self._resolve("Brand® (generic)", [{"type": "DRUG", "name": "rosuvastatin 10 mg"}],
                                    {"rosuvastatin": "CHEMBL1496", "Brand": "CHEMBL0"})
        self.assertEqual(drug["chembl_id"], "CHEMBL1496")
        self.assertNotIn("Brand", tried)


class MalformedChanges(unittest.TestCase):
    def test_note_strings_in_changes_are_left_out(self):
        from app.agents.analysis import _keep_change_objects
        m = {"changes": [{"biomarker": "LDL-C", "pct_change": -17}, "HDL-C change not placebo-adjusted"]}
        self.assertEqual(_keep_change_objects(m), ["HDL-C change not placebo-adjusted"])
        self.assertEqual(m["changes"], [{"biomarker": "LDL-C", "pct_change": -17}])

    def test_well_formed_output_is_not_touched(self):
        from app.agents.analysis import _keep_change_objects
        m = {"changes": None}
        self.assertEqual(_keep_change_objects(m), [])
        self.assertIsNone(m["changes"])


class NormaliseAnalystOutput(unittest.TestCase):
    def test_names_numbers_ids_units_and_ci_are_cleaned(self):
        from app.agents.analysis import normalise_mechanism
        m = {"intended_biomarker": ["LDL cholesterol"], "events": "1,612", "followup_years": 0,
             "observed_ci": ["0.91", 0.78], "events_fact_ids": "f_1",
             "changes": [{"biomarker": "LDL cholesterol", "abs_change": "-1.2 mmol/L", "fact_ids": "f_2"},
                         {"biomarker": "Apo B", "abs_change": -0.3, "unit": "g/L", "baseline": 0}]}
        notes = normalise_mechanism(m)
        self.assertTrue(notes)
        self.assertEqual(m["intended_biomarker"], "LDL-C")
        self.assertEqual(m["events"], 1612)
        self.assertIsNone(m["followup_years"])
        self.assertEqual(m["observed_ci"], [0.78, 0.91])
        self.assertEqual(m["events_fact_ids"], ["f_1"])
        ldl, apob = m["changes"]
        self.assertEqual((ldl["biomarker"], ldl["abs_change"], ldl["unit"], ldl["fact_ids"]), ("LDL-C", -1.2, "mmol/L", ["f_2"]))
        self.assertEqual((apob["biomarker"], apob["abs_change"], apob["unit"], apob["baseline"]), ("apoB", -30.0, "mg/dL", None))

    def test_well_formed_output_is_untouched(self):
        from app.agents.analysis import normalise_mechanism
        m = {"intended_biomarker": "LDL-C", "events": 1612, "followup_years": 2.2, "observed_ci": [0.69, 0.80],
             "changes": [{"biomarker": "LDL-C", "abs_change": -1.45, "unit": "mmol/L", "fact_ids": ["f_1"]}]}
        before = repr(m)
        self.assertEqual(normalise_mechanism(m), [])
        self.assertEqual(repr(m), before)

    def test_critic_issues_given_as_strings_become_objects(self):
        from app.agents.analysis import normalise_issues
        data = {"issues": ["events not verified", {"severity": "high", "issue": "x"}]}
        self.assertEqual(normalise_issues(data)[0], {"severity": "medium", "issue": "events not verified"})
