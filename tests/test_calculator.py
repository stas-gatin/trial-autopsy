import unittest

from app.core.calculator import (BiomarkerChange, CaseInput, evaluate, minimal_detectable_hr, power,
                                 time_factor)


class PowerTests(unittest.TestCase):
    def test_reproduces_published_design_power(self):
        # Lp(a)HORIZON design paper: 993 events give >90% power for HR 0.80.
        self.assertGreater(power(0.80, 993), 0.90)
        # FOURIER design: 1630 events, 90% power for 15% RRR.
        self.assertAlmostEqual(power(0.85, 1630), 0.90, delta=0.03)

    def test_no_benefit_gives_alpha(self):
        self.assertAlmostEqual(power(1.0, 2000), 0.025)

    def test_bigger_trial_detects_smaller_effects(self):
        # More events -> the smallest detectable effect is closer to HR 1.
        self.assertGreater(minimal_detectable_hr(5000), minimal_detectable_hr(1000))


class TimeFactorTests(unittest.TestCase):
    def test_bounds(self):
        self.assertAlmostEqual(time_factor(5), 1.0)
        self.assertAlmostEqual(time_factor(10), 1.0)
        self.assertAlmostEqual(time_factor(0), 0.0)
        self.assertLess(time_factor(1), time_factor(3))


class VerdictTests(unittest.TestCase):
    def test_discordant_ldl_uses_apob(self):
        case = CaseInput(
            case_id="cetp_like", mode="autopsy", target_genetics="causal", intended_biomarker="HDL-C",
            changes=[BiomarkerChange("LDL-C", pct_change=-37), BiomarkerChange("apoB", pct_change=-15, abs_change=-12)],
            followup_years=2.2, events=1555)
        result = evaluate(case)
        self.assertIn("apoB", result.basis)
        self.assertEqual(result.category, "effect_too_small")

    def test_counterfactual_bigger_apob_flips_to_success(self):
        # Same trial, but apoB falls 40 mg/dL instead of 12: the verdict must change.
        case = CaseInput(
            case_id="counterfactual", mode="autopsy", target_genetics="causal", intended_biomarker="HDL-C",
            changes=[BiomarkerChange("LDL-C", pct_change=-37), BiomarkerChange("apoB", pct_change=-45, abs_change=-40)],
            followup_years=2.2, events=1555)
        self.assertEqual(evaluate(case).outcome, "success")

    def test_non_causal_target(self):
        case = CaseInput(
            case_id="lppla2_like", mode="autopsy", target_genetics="not_causal",
            intended_biomarker="Lp-PLA2 activity",
            changes=[BiomarkerChange("Lp-PLA2 activity", pct_change=-65)], followup_years=3.7, events=1588)
        self.assertEqual(evaluate(case).category, "target_not_causal")

    def test_unquantified_pathway_is_not_computable(self):
        case = CaseInput(
            case_id="il1_like", mode="autopsy", target_genetics="uncertain", intended_biomarker="hsCRP",
            pathway="IL-1beta", changes=[BiomarkerChange("LDL-C", pct_change=0)], followup_years=3.7, events=855)
        result = evaluate(case)
        self.assertIsNone(result.expected_hr)
        self.assertEqual(result.outcome, "not_computable")

    def test_lpa_in_nmol_is_converted(self):
        nmol = CaseInput(case_id="x", mode="forecast", target_genetics="causal", intended_biomarker="Lp(a)",
                         changes=[BiomarkerChange("Lp(a)", unit="nmol/L", pct_change=-97, baseline=260)])
        mgdl = CaseInput(case_id="x", mode="forecast", target_genetics="causal", intended_biomarker="Lp(a)",
                         changes=[BiomarkerChange("Lp(a)", unit="mg/dL", pct_change=-97, baseline=260 / 2.4)])
        self.assertAlmostEqual(evaluate(nmol).expected_hr, evaluate(mgdl).expected_hr, places=3)

    def test_unquantified_main_mechanism_abstains_instead_of_false_failure(self):
        # GLP-1-like: big weight loss, tiny lipid and BP changes. Must NOT be called a failure.
        glp1 = CaseInput(case_id="glp1", mode="forecast", target_genetics="causal", intended_biomarker="body weight",
                         changes=[BiomarkerChange("LDL-C", abs_change=-0.08), BiomarkerChange("HbA1c", abs_change=-0.3)],
                         sbp_change_mmhg=-3.3, followup_years=3.3, events=1270)
        r = evaluate(glp1)
        self.assertEqual(r.outcome, "not_computable")
        self.assertIsNone(r.expected_hr)
        self.assertIsNotNone(r.partial_expected_hr)
        self.assertLess(r.partial_expected_hr, 1.0)
        # Colchicine-like: intended hsCRP, no lipid change, no explicit pathway.
        colchicine = CaseInput(case_id="colch", mode="autopsy", target_genetics="uncertain", intended_biomarker="hsCRP",
                               changes=[BiomarkerChange("hsCRP", pct_change=-30)], followup_years=2.5, events=500)
        self.assertEqual(evaluate(colchicine).outcome, "not_computable")
        # SGLT2-like: intended HbA1c.
        sglt2 = CaseInput(case_id="sglt2", mode="autopsy", target_genetics="uncertain", intended_biomarker="HbA1c",
                          changes=[BiomarkerChange("HbA1c", abs_change=-0.5)], followup_years=3.1, events=772)
        self.assertEqual(evaluate(sglt2).outcome, "not_computable")

    def test_biomarker_names_are_canonicalised(self):
        case = CaseInput(case_id="vista_like", mode="autopsy", target_genetics="uncertain",
                         intended_biomarker="sPLA2-IIA mass", changes=[BiomarkerChange("sPLA2-IIA mass", pct_change=-67)],
                         followup_years=0.3, events=245)
        self.assertEqual(evaluate(case).category, "biomarker_not_causal")

    def test_harm_beyond_biomarkers_is_off_target(self):
        case = CaseInput(
            case_id="torcetrapib_like", mode="autopsy", target_genetics="causal", intended_biomarker="HDL-C",
            changes=[BiomarkerChange("apoB", abs_change=-10.7)], sbp_change_mmhg=5.4,
            followup_years=1.5, events=837, observed_hr=1.25, observed_ci=(1.09, 1.44))
        self.assertEqual(evaluate(case).category, "off_target_harm")

    def test_blood_pressure_lowering_alone_counts_as_a_causal_effect(self):
        # SPRINT-like: no lipid change, systolic BP 13 mmHg lower, 562 events over 3.3 years.
        case = CaseInput(
            case_id="sprint_like", mode="autopsy", target_genetics="causal", intended_biomarker="SBP",
            changes=[], sbp_change_mmhg=-13.1, followup_years=3.26, events=562)
        result = evaluate(case)
        self.assertGreater(result.causal_rrr, 0.15)
        self.assertAlmostEqual(result.causal_rrr, 1 - result.expected_hr, places=3)
        self.assertNotEqual(result.category, "effect_too_small")

    def test_an_unmeasured_ldl_change_is_unknown_not_zero(self):
        # TNT-like: LDL-C is the mechanism, but no between-arm value was found in the documents.
        common = dict(case_id="tnt_like", mode="autopsy", intended_biomarker="LDL-C", followup_years=4.9, events=982)
        missing = evaluate(CaseInput(target_genetics="causal", changes=[BiomarkerChange("LDL-C")], **common))
        self.assertEqual((missing.outcome, missing.category), ("not_computable", "insufficient_data"))
        self.assertIsNone(missing.expected_hr)
        self.assertEqual(missing.weakest_link, "LDL-C change not measured")
        # A target that genetics shows is not causal fails whatever the size.
        non_causal = evaluate(CaseInput(target_genetics="not_causal", changes=[BiomarkerChange("LDL-C")], **common))
        self.assertEqual((non_causal.outcome, non_causal.category), ("failure", "target_not_causal"))

    def test_blood_pressure_given_as_a_biomarker_row_is_used_once(self):
        row = BiomarkerChange("SBP", abs_change=-13.1, unit="mmHg")
        common = dict(case_id="sprint_like", mode="autopsy", target_genetics="causal", intended_biomarker="SBP",
                      followup_years=3.26, events=562)
        from_row = evaluate(CaseInput(changes=[row], **common))
        from_field = evaluate(CaseInput(changes=[], sbp_change_mmhg=-13.1, **common))
        both = evaluate(CaseInput(changes=[row], sbp_change_mmhg=-13.1, **common))
        self.assertEqual(from_row.expected_hr, from_field.expected_hr)
        self.assertEqual(both.expected_hr, from_field.expected_hr)


if __name__ == "__main__":
    unittest.main()


class GeneralInputRules(unittest.TestCase):
    """Rules that must hold for any drug: names, units, and missing values."""

    common = dict(case_id="x", mode="autopsy", target_genetics="causal", followup_years=4.0, events=1500)

    def test_biomarker_names_are_matched_as_words(self):
        from app.core.calculator import canonical
        self.assertEqual(canonical("non HDL cholesterol"), "non-HDL-C")
        self.assertEqual(canonical("Apo B"), "apoB")
        self.assertEqual(canonical("Lp (a)"), "Lp(a)")
        self.assertEqual(canonical("diastolic blood pressure"), "DBP")
        self.assertEqual(canonical("VLDL cholesterol"), "VLDL-C")
        self.assertEqual(canonical("IL-18"), "IL-18")
        self.assertEqual(canonical("secretory phospholipase A2"), "sPLA2-IIA")

    def test_units_are_converted_whatever_the_name(self):
        mmol = evaluate(CaseInput(intended_biomarker="LDL-C", changes=[BiomarkerChange("LDL-C", abs_change=-1.0)],
                                  **self.common))
        mgdl = evaluate(CaseInput(intended_biomarker="LDL cholesterol",
                                  changes=[BiomarkerChange("LDL cholesterol", abs_change=-38.67, unit="mg/dL")],
                                  **self.common))
        self.assertAlmostEqual(mmol.ldl_equivalent_mmol, mgdl.ldl_equivalent_mmol, places=3)
        gl = evaluate(CaseInput(intended_biomarker="apoB", changes=[BiomarkerChange("Apo B", abs_change=-0.3, unit="g/L")],
                                **self.common))
        self.assertAlmostEqual(gl.ldl_equivalent_mmol, 30 / 29.2, places=2)

    def test_a_triglyceride_drug_is_sized_by_non_hdl_or_apob_never_by_ldl_alone(self):
        only_ldl = evaluate(CaseInput(intended_biomarker="triglycerides",
                                      changes=[BiomarkerChange("triglycerides", pct_change=-20),
                                               BiomarkerChange("LDL-C", pct_change=3, baseline=2.0)], **self.common))
        self.assertEqual(only_ldl.outcome, "not_computable")
        with_nonhdl = evaluate(CaseInput(intended_biomarker="triglycerides",
                                         changes=[BiomarkerChange("LDL-C", pct_change=3, baseline=2.0),
                                                  BiomarkerChange("non-HDL-C", abs_change=-0.4)], **self.common))
        self.assertAlmostEqual(with_nonhdl.ldl_equivalent_mmol, 0.4, places=3)

    def test_an_lpa_drug_is_not_sized_by_a_small_ldl_change(self):
        r = evaluate(CaseInput(intended_biomarker="Lp(a)", changes=[BiomarkerChange("LDL-C", abs_change=-0.05)],
                               **self.common))
        self.assertEqual((r.outcome, r.category), ("not_computable", "insufficient_data"))

    def test_no_intended_biomarker_and_no_value_is_not_a_failure(self):
        r = evaluate(CaseInput(intended_biomarker="", changes=[], **self.common))
        self.assertEqual(r.outcome, "not_computable")

    def test_zero_follow_up_is_unknown(self):
        r = evaluate(CaseInput(intended_biomarker="LDL-C", changes=[BiomarkerChange("LDL-C", abs_change=-1.0)],
                               **{**self.common, "followup_years": 0}))
        self.assertLess(r.expected_hr, 1.0)
        self.assertIn("follow-up unknown, effect shown at 5 years", r.assumptions)

    def test_discordance_needs_a_real_gap(self):
        small = evaluate(CaseInput(intended_biomarker="LDL-C", changes=[BiomarkerChange("LDL-C", pct_change=0, baseline=2.5),
                                                                        BiomarkerChange("apoB", pct_change=-5)], **self.common))
        self.assertEqual(small.basis, "LDL-C")
        opposite = evaluate(CaseInput(intended_biomarker="LDL-C", changes=[BiomarkerChange("LDL-C", pct_change=-20, baseline=2.5),
                                                                           BiomarkerChange("apoB", pct_change=20, baseline=90),
                                                                           BiomarkerChange("non-HDL-C", abs_change=0.2)],
                                      **self.common))
        self.assertTrue(opposite.basis.startswith("non-HDL-C"))

    def test_the_first_row_for_a_biomarker_is_used(self):
        r = evaluate(CaseInput(intended_biomarker="LDL-C", changes=[BiomarkerChange("LDL-C", abs_change=-1.0),
                                                                    BiomarkerChange("LDL cholesterol", abs_change=-0.1)],
                               **self.common))
        self.assertAlmostEqual(r.ldl_equivalent_mmol, 1.0)
