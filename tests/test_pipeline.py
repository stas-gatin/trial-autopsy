import json
import os
import tempfile
import unittest
from pathlib import Path

from app.agents.analysis import check_inputs
from app.llm.generator import parse_json, request_key
from app.tools.documents import Document, verify_quote, visible_on
from app.tools.europepmc import matches_record

ROOT = Path(__file__).resolve().parents[1]


class QuoteTests(unittest.TestCase):
    def test_verbatim_quote_passes_with_whitespace_and_dash_differences(self):
        text = "Lp(a) was reduced by 80%\n with 20 mg weekly − versus 6% with placebo."
        self.assertTrue(verify_quote(text, "reduced by 80% with 20 mg weekly - versus 6%"))

    def test_paraphrase_fails(self):
        self.assertFalse(verify_quote("Lp(a) was reduced by 80% with 20 mg weekly.", "Lp(a) fell 80% at 20 mg"))

    def test_short_quote_fails(self):
        self.assertFalse(verify_quote("HR 0.85", "0.85"))


class TimeMachineTests(unittest.TestCase):
    def test_documents_after_cutoff_are_hidden(self):
        from datetime import date
        doc = Document("pmid:1", "journal", "t", "x", pub_date="2017-05-18")
        self.assertFalse(visible_on(doc, date(2015, 10, 12)))
        self.assertTrue(visible_on(doc, date(2017, 6, 1)))
        self.assertTrue(visible_on(doc, None))


class FullTextGuardTests(unittest.TestCase):
    def test_mislinked_full_text_is_rejected(self):
        record = {"title": "Effect of Pelacarsen on Lipoprotein(a) Cholesterol and Corrected LDL Cholesterol"}
        self.assertFalse(matches_record(record, "Aspirin use in COVID-19 patients with chronic kidney disease ..."))
        self.assertTrue(matches_record(record, "Effect of pelacarsen on lipoprotein(a) cholesterol and corrected ..."))


class SecTests(unittest.TestCase):
    def test_announcement_detection(self):
        from app.tools.sec import announces_result
        yes = Document("sec:1", "press_release", "t", "Lp(a)HORIZON did not meet its primary endpoint ...")
        no = Document("sec:2", "press_release", "t", "Quarterly revenue grew; Lp(a)HORIZON enrolment continues.")
        self.assertTrue(announces_result(yes, ["Lp(a)HORIZON"]))
        self.assertFalse(announces_result(no, ["Lp(a)HORIZON"]))


class GeneratorTests(unittest.TestCase):
    def test_request_key_is_deterministic(self):
        self.assertEqual(request_key("reasoning", "s", "p"), request_key("reasoning", "s", "p"))
        self.assertNotEqual(request_key("reasoning", "s", "p"), request_key("critic", "s", "p"))

    def test_parse_json_fenced_and_bare(self):
        self.assertEqual(parse_json('text ```json\n{"a": 1}\n``` more'), {"a": 1})
        self.assertEqual(parse_json('Here: {"facts": []}'), {"facts": []})


class InputCheckTests(unittest.TestCase):
    def test_number_must_match_cited_fact(self):
        facts = {"f_1": {"value": -12}, "f_2": {"value": 1555}}
        good = {"changes": [{"biomarker": "apoB", "abs_change": -12, "fact_ids": ["f_1"]}],
                "events": 1555, "events_fact_ids": ["f_2"]}
        bad = {"changes": [{"biomarker": "apoB", "abs_change": -20, "fact_ids": ["f_1"]}],
               "events": 1555, "events_fact_ids": ["f_9"]}
        self.assertEqual(check_inputs(good, facts), [])
        issues = check_inputs(bad, facts)
        self.assertTrue(any("does not match" in i for i in issues))
        self.assertTrue(any("unknown" in i for i in issues))

    def test_mg_dl_to_mmol_conversion_is_accepted(self):
        facts = {"f_1": {"value": -16.7}}
        m = {"changes": [{"biomarker": "LDL-C", "abs_change": -0.432, "fact_ids": ["f_1"]}]}
        self.assertEqual(check_inputs(m, facts), [])


class WorldModelTests(unittest.TestCase):
    def test_ingest_is_idempotent_and_runs_are_isolated(self):
        from unittest import mock

        from app import config
        from app.engines import world_model as wm
        # the store and its audit log go to a temporary folder, never into the repository's data/ or logs/
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(wm, "WORLD_DB_DIR", Path(tmp)), \
                mock.patch.object(config, "LOGS", Path(tmp)):
            store = wm.WorldStoreService("run-a", db_name="t.db")
            ingest = wm.IngestPipelineService(store)
            doc = Document("pmid:1", "journal", "t", "apoB fell 15%", pub_date="2015-01-01")
            ingest.upsert_source(doc)
            args = dict(case_id="c", entity_id="biomarker:apob", key="biomarker_change", value=-15,
                        source_id="pmid:1", quote="apoB fell 15%", quote_verified=True, agent="test")
            _, first = ingest.ingest_fact(**args)
            _, second = ingest.ingest_fact(**args)
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(len(wm.ContextBuilderService(store).facts("c")), 1)
            fresh = wm.WorldStoreService("run-a", db_name="t.db")
            self.assertEqual(len(wm.ContextBuilderService(fresh).facts("c")), 0)


@unittest.skipUnless((ROOT / "data" / "sample_cache" / "http").exists(), "sample cache not exported")
class SampleModeEndToEnd(unittest.TestCase):
    def test_recorded_pelacarsen_case_runs_offline(self):
        os.environ["SAMPLE_MODE"] = "true"
        try:
            from app import orchestrator
            request = json.loads((ROOT / "input_examples" / "example_1_pelacarsen_autopsy.json").read_text())
            result = orchestrator.run(request["input"], run_id="test-sample-pelacarsen")
        finally:
            os.environ["SAMPLE_MODE"] = "false"
        self.assertEqual(result["status"], "success", result.get("error"))
        verdict = result["output"]["verdict"]
        self.assertEqual(verdict["outcome"], "failure")
        self.assertTrue(all(f["quote"] for f in result["output"]["facts"]))
        usage = result["output"]["usage"]
        self.assertGreater(usage["Investigator"]["calls"], 0)
        # The demo case replays its NVIDIA Nemotron recordings, which carry the usage NVIDIA's API reported.
        self.assertTrue(all("nemotron" in m for m in result["output"]["models_used"].values()), result["output"]["models_used"])
        self.assertGreater(usage["Extraction"]["input_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
