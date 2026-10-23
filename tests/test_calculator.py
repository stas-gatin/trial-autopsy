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
