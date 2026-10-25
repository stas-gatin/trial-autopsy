import unittest

from app.agents.analysis import check_inputs, complete_inputs
from app.agents.case_builder import change_direction, sentence_before


class DirectionFromQuote(unittest.TestCase):
    def test_nearest_direction_word_decides(self):
        q = ("Niacin use significantly increased HDL-C by 29% and decreased triglycerides by 23% and "
             "low-density lipoprotein cholesterol (LDL-C) by 8%")
        self.assertEqual(change_direction(q, 8), -1)
        self.assertEqual(change_direction(q, 29), 1)

    def test_a_direction_word_of_another_clause_is_not_used(self):
        # "increased" belongs to HDL-C; LDL-C's own clause has a verb and no direction, so the direction is unknown
        self.assertEqual(change_direction("increased HDL-C by 133%, and LDL-C was 31.1%", 31.1), 0)
        self.assertEqual(change_direction("increased HDL-C by 133%, and LDL-C was 31.1% relatively lower", 31.1), -1)
        # "elevated" describes the patients, not the change
        self.assertEqual(change_direction("In patients with elevated Lp(a), the placebo-adjusted change in Lp(a) was 35%", 35), 0)
        self.assertEqual(change_direction("LDL-C decreased 30% while HDL increased 10%", 30), -1)
        self.assertEqual(change_direction("LDL-C decreased 30% while HDL increased 10%", 10), 1)

    def test_a_direction_word_after_the_number(self):
        self.assertEqual(change_direction("a 27.2% decrease in LDL-C", 27.2), -1)
        self.assertEqual(change_direction("LDL-C fell by 50% (p<0.001) and HDL rose", 50), -1)

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


class KeepVerifiedInputs(unittest.TestCase):
    facts = {"f_1": {"fact_id": "f_1"}, "f_2": {"fact_id": "f_2"}}
    previous = {"events": 1640, "events_fact_ids": ["f_2"],
                "changes": [{"biomarker": "LDL-C", "abs_change": -0.63, "fact_ids": ["f_1"]}]}

    def test_a_dropped_verified_change_is_restored(self):
        from app.agents.analysis import keep_verified_inputs
        current = {"events": None, "changes": [{"biomarker": "", "abs_change": None}]}
        kept = keep_verified_inputs(self.previous, current, [{"issue": "remove the trial's own HR from hypotheses"}], self.facts)
        self.assertEqual(len(kept), 2)
        self.assertIn({"biomarker": "LDL-C", "abs_change": -0.63, "fact_ids": ["f_1"]}, current["changes"])
        self.assertEqual(current["events"], 1640)

    def test_a_change_the_critic_questioned_is_left_as_revised(self):
        from app.agents.analysis import keep_verified_inputs
        current = {"events": 1640, "changes": []}
        kept = keep_verified_inputs(self.previous, current, [{"issue": "LDL-C value is not placebo-adjusted"}], self.facts)
        self.assertEqual(kept, [])
        self.assertEqual(current["changes"], [])


class ReadoutStatements(unittest.TestCase):
    def classify(self, text):
        from app.agents.analysis import classify_readout
        hit = classify_readout(text)
        return hit[0] if hit else None

    def test_stopping_is_read_with_its_reason(self):
        self.assertEqual(self.classify("The trial was stopped early for efficacy"), "success")
        self.assertEqual(self.classify("Outcomes Study Closes Early Due To Unequivocal Evidence Of Benefit"), "success")
        self.assertEqual(self.classify("The study was stopped for futility"), "failure")
        self.assertEqual(self.classify("KOWA TO DISCONTINUE K-877 (PEMAFIBRATE) PROMINENT CARDIOVASCULAR OUTCOMES STUDY"), "failure")

    def test_common_announcement_wordings(self):
        self.assertEqual(self.classify("The study will be stopped early because there is unequivocal evidence of a "
                                       "reduction in cardiovascular morbidity"), "success")
        self.assertEqual(self.classify("Combination lipid therapies do not reduce combined cardiovascular events"), "failure")
        self.assertEqual(self.classify("the DSMB concluded that the primary endpoint was unlikely to be met"), "failure")
        self.assertEqual(self.classify("a statistically significant 21 percent reduction in risk"), "success")
        self.assertEqual(self.classify("Patients receiving hemodialysis did not benefit from statin therapy"), "failure")

    def test_patients_reaching_an_endpoint_is_an_event_not_a_result(self):
        self.assertIsNone(self.classify("396 patients in the rosuvastatin group and 408 in the placebo group reached the "
                                        "primary end point"))

    def test_words_about_patients_are_not_a_readout(self):
        self.assertIsNone(self.classify("discontinuation rates were similar between groups"))
        self.assertIsNone(self.classify("treatment was discontinued in 5% of patients"))

    def test_an_explicit_primary_statement_wins(self):
        self.assertEqual(self.classify("did not meet its primary endpoint but significantly reduced LDL-C"), "failure")


class DrugUnderTest(unittest.TestCase):
    def test_the_experimental_arm_drug_is_tried_before_the_background_one(self):
        from types import SimpleNamespace
        from unittest import mock
        from app.agents import intake
        trial = {"arm_groups": [{"label": "Eze/Simva", "type": "EXPERIMENTAL"},
                                {"label": "Simva", "type": "ACTIVE_COMPARATOR"}],
                 "interventions": [{"type": "DRUG", "name": "simvastatin", "arms": ["Eze/Simva", "Simva"]},
                                   {"type": "DRUG", "name": "ezetimibe", "arms": ["Eze/Simva"]}]}
        found = lambda name, tracer=None: {"id": "CHEMBL" + name, "name": name}
        profile = {"name": "x", "mechanismsOfAction": {"rows": []}, "synonyms": [], "tradeNames": []}
        state = SimpleNamespace(request={}, trial=trial, tracer=None, warnings=[], drug=None)
        with mock.patch.object(intake.opentargets, "find_drug", side_effect=found), \
                mock.patch.object(intake.opentargets, "drug_profile", return_value=profile):
            intake._resolve_drug(state)
        self.assertEqual(state.drug["chembl_id"], "CHEMBLezetimibe")


class InconsistentChange(unittest.TestCase):
    def test_absolute_and_percent_that_disagree_are_flagged(self):
        from app.agents.analysis import check_inputs
        m = {"changes": [{"biomarker": "LDL-C", "abs_change": -0.1, "pct_change": -50, "baseline": 2.5, "fact_ids": []}]}
        self.assertTrue(any("disagree" in i for i in check_inputs(m, {})))
        m["changes"][0]["abs_change"] = -1.25
        self.assertFalse(any("disagree" in i for i in check_inputs(m, {})))


class NegatedReductions(unittest.TestCase):
    def test_a_negated_reduction_is_a_failure(self):
        from app.agents.analysis import classify_readout
        hit = classify_readout("adding niacin to statin therapy did not significantly further reduce the risk of "
                               "the combination of coronary deaths")
        self.assertEqual(hit[0], "failure")


class OutcomeAndCategoryAgree(unittest.TestCase):
    def test_a_success_is_not_explained_by_a_failure_cause(self):
        from types import SimpleNamespace
        from unittest import mock
        from app.agents import analysis
        state = SimpleNamespace(calc=SimpleNamespace(power=0.48, outcome="failure", category="effect_too_small",
                                                     secondary=[], weakest_link="", expected_hr=0.89),
                                investigation={"primary_category": "effect_too_small",
                                               "secondary_categories": ["narrow_success"], "confidence": "high"},
                                verification={"status": "ok"})
        with mock.patch.object(analysis, "actual_outcome", return_value=("success", "readout")):
            v = analysis.verdict_block(state)
        self.assertEqual(v["primary_category"], "narrow_success")


class DesignPaper(unittest.TestCase):
    def test_only_this_trials_design_paper_counts(self):
        from app.agents.case_builder import is_design_paper
        own = {"title": "Rationale and design of IMPROVE-IT", "abstract": "The trial will continue until 5250 events."}
        other = {"title": "Rationale and design of the SEARCH trial", "abstract": "Unlike IMPROVE-ITS ..."}
        results = {"title": "IMPROVE-IT results", "abstract": "IMPROVE-IT met its primary endpoint"}
        self.assertTrue(is_design_paper(own, "IMPROVE-IT"))
        self.assertFalse(is_design_paper(other, "IMPROVE-IT"))
        self.assertFalse(is_design_paper(results, "IMPROVE-IT"))
        self.assertTrue(is_design_paper({"title": "JUPITER: baseline characteristics", "abstract": ""},
                                        "JUPITER (Justification for the Use of Statins in Prevention)"))


class NonSignificant(unittest.TestCase):
    def test_a_non_significant_reduction_is_not_a_success(self):
        from app.agents.analysis import classify_readout
        self.assertEqual(classify_readout("produced only a small, non-significant reduction in CHD events")[0], "failure")
        self.assertEqual(classify_readout("the difference was too small to show a difference in death rates")[0], "failure")
        self.assertEqual(classify_readout("Significantly Reduced Major Vascular Events")[0], "success")


class PrimaryResultWordings(unittest.TestCase):
    def test_significance_and_p_values_stated_with_the_primary_endpoint(self):
        from app.agents.analysis import classify_readout
        self.assertEqual(classify_readout("an 8 percent reduction (p=0.12) in the combined primary endpoint, which was "
                                          "not statistically significant")[0], "failure")
        self.assertEqual(classify_readout("There was a statistically significant difference (p=0.005) in event rates")[0],
                         "success")
        self.assertEqual(classify_readout("adjusted HR 1·00, p=0·943 for the primary end-point")[0], "failure")
        self.assertEqual(classify_readout("rosuvastatin did not affect clinical outcomes in heart failure")[0], "failure")
        self.assertEqual(classify_readout("no benefit in those with lower blood pressure and even a tendency to harm")[0],
                         "failure")   # 'tendency' does not contain the stop word 'end'


class ThresholdIsNotABaseline(unittest.TestCase):
    def test_an_entry_threshold_cited_as_the_ldl_baseline_is_left_out(self):
        from app.agents.analysis import complete_inputs
        facts = {"t": {"fact_id": "t", "key": "inclusion_threshold", "value": 70, "unit": "mg/dL"},
                 "p": {"fact_id": "p", "key": "biomarker_change", "value": -61, "unit": "%"}}
        m = {"changes": [{"biomarker": "LDL-C", "pct_change": -61, "baseline": 1.81, "unit": "mmol/L",
                          "fact_ids": ["p", "t"]}]}
        filled = complete_inputs(m, facts)
        self.assertIsNone(m["changes"][0]["baseline"])
        self.assertTrue(any("entry threshold" in x for x in filled))

    def test_a_measured_baseline_and_an_lpa_threshold_are_kept(self):
        from app.agents.analysis import complete_inputs
        facts = {"t": {"fact_id": "t", "key": "inclusion_threshold", "value": 70, "unit": "mg/dL"}}
        m = {"changes": [{"biomarker": "LDL-C", "pct_change": -61, "baseline": 2.4, "unit": "mmol/L", "fact_ids": ["t"]},
                         {"biomarker": "Lp(a)", "pct_change": -80, "baseline": 70, "unit": "mg/dL", "fact_ids": ["t"]}]}
        complete_inputs(m, facts)
        self.assertEqual(m["changes"][0]["baseline"], 2.4)
        self.assertEqual(m["changes"][1]["baseline"], 70)
