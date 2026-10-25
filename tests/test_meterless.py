import os
import unittest

from app.engines import meterless


class MeterlessClientTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)

    def test_unreachable_service_switches_the_mirror_off_without_failing(self):
        os.environ["METERLESS_URL"] = "http://127.0.0.1:9"  # nothing listens here
        os.environ["SOURCE_METERLESS"] = "true"
        client = meterless.MeterlessClient("unit test/run")
        self.assertEqual(client.namespace, "unit-test-run")
        self.assertFalse(client.active)
        self.assertIsNotNone(client.error)
        client.fact(fact_id="f1", entity_id="drug:x", key="k", value=1)  # no exception
        self.assertIsNone(client.stats())

    def test_switched_off_in_settings(self):
        os.environ["SOURCE_METERLESS"] = "false"
        client = meterless.MeterlessClient("run")
        self.assertFalse(client.active)
        self.assertIsNone(client.error)


class NightshiftRecordingTests(unittest.TestCase):
    """A Nightshift answer is filed under the model that actually answered, never under the lane's NVIDIA model."""

    def _generate(self, answered):
        from unittest import mock
        from app import config
        from app.llm import generator
        usage = {"input_tokens": 1, "output_tokens": 1, "model": answered, "route": "meterless/deep",
                 "lane": "nvidia/nemotron-3-ultra-550b-a55b"}
        with mock.patch.object(config, "agent_spec", return_value=config.Role("meterless", "meterless/deep")), \
                mock.patch.object(config, "sample_mode", return_value=False), \
                mock.patch.object(config, "provider_key", return_value="token"), \
                mock.patch.object(config, "_flag", return_value=False), \
                mock.patch.object(generator, "_call_meterless", return_value=("ok", dict(usage))), \
                mock.patch.object(generator, "_record") as record:
            out = generator.generate("reasoning", "sys", "prompt", agent="Investigator")
        return out, record.call_args.kwargs["cache_model"]

    def test_openrouter_lane_is_not_filed_as_ultra(self):
        out, folder = self._generate("openrouter/nvidia/nemotron-3.5-lightning")
        self.assertEqual(folder, "openrouter/nvidia/nemotron-3.5-lightning")
        self.assertEqual(out.model, "openrouter/nvidia/nemotron-3.5-lightning")

    def test_nvidia_lane_is_filed_under_its_model(self):
        _, folder = self._generate("nvidia/nemotron-3-ultra-550b-a55b")
        self.assertEqual(folder, "nvidia/nemotron-3-ultra-550b-a55b")

    def test_reasoning_is_removed_even_without_the_opening_tag(self):
        from app.llm.generator import strip_reasoning
        self.assertEqual(strip_reasoning('The user wants JSON. So output it.</think>{"ok": true}'), '{"ok": true}')
        self.assertEqual(strip_reasoning('<think>plan</think>\n{"ok": true}'), '{"ok": true}')
        self.assertEqual(strip_reasoning('{"ok": true}'), '{"ok": true}')
        self.assertEqual(strip_reasoning('<think>cut off before the answer'), '')

    def test_route_echo_falls_back_to_the_lane_model(self):
        _, folder = self._generate("meterless/deep")
        self.assertEqual(folder, "nvidia/nemotron-3-ultra-550b-a55b")


if __name__ == "__main__":
    unittest.main()


class JsonRetryTests(unittest.TestCase):
    """A reply that neither parses nor repairs is asked once more, under its own recording; a normal reply is not."""

    def _run(self, replies):
        from unittest import mock
        from app.llm import generator
        calls = []

        def fake(role, system, prompt, tag="", agent=None):
            calls.append(tag)
            return mock.Mock(text=replies[len(calls) - 1])

        with mock.patch.object(generator, "generate", side_effect=fake):
            data, _ = generator.generate_json("critic", "system", "prompt", tag="case:critic:0")
        return data, calls

    def test_reply_without_json_is_retried_once(self):
        # A cut-off reply that quotes ['f_1'] still holds no JSON object; the repair pass answers in prose too.
        data, calls = self._run(["Let me analyze this. Facts ['f_1'] do not match", "I can't verify that.",
                                 '{"issues": []}'])
        self.assertEqual(data, {"issues": []})
        self.assertEqual(calls, ["case:critic:0", "case:critic:0:json-repair", "case:critic:0:retry"])

    def test_normal_reply_is_not_retried(self):
        data, calls = self._run(['{"issues": [1]}'])
        self.assertEqual(data, {"issues": [1]})
        self.assertEqual(calls, ["case:critic:0"])

    def test_extraction_can_opt_out_of_the_retry(self):
        from unittest import mock
        from app.llm import generator
        replies = iter(["Facts ['f_1'] do not", "I can't verify that."])
        with mock.patch.object(generator, "generate", side_effect=lambda *a, **k: mock.Mock(text=next(replies))):
            with self.assertRaises(ValueError):
                generator.generate_json("extraction", "system", "prompt", tag="case:extract:x", retry=False)


class FactsBelongToEachRun(unittest.TestCase):
    def test_two_runs_of_one_case_keep_their_facts(self):
        import tempfile
        from unittest import mock
        from app import config
        from app.engines import world_model
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(world_model, "WORLD_DB_DIR", __import__("pathlib").Path(tmp)), \
                mock.patch.object(config, "LOGS", __import__("pathlib").Path(tmp)), \
                mock.patch.dict("os.environ", {"SOURCE_METERLESS": "false"}):
            runs = []
            for rid in ("run-a", "run-b"):
                store = world_model.WorldStoreService(rid)
                ingest = world_model.IngestPipelineService(store)
                from app.tools.documents import Document
                ingest.upsert_source(Document(source_id="s1", type="journal", title="t", text="q", url="",
                                              pub_date="2020-01-01", access="abstract_only"))
                ingest.ingest_fact(case_id="x", entity_id="drug:x", key="k", value=1, source_id="s1", quote="q",
                                   quote_verified=True, agent="test")
                runs.append(store)
            for store in runs:
                self.assertEqual(len(store.query("SELECT 1 FROM facts WHERE run_id=?", (store.run_id,))), 1)

    def test_a_run_does_not_see_source_metadata_from_an_earlier_run(self):
        import tempfile
        from unittest import mock
        from app import config
        from app.engines import world_model
        from app.tools.documents import Document
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(world_model, "WORLD_DB_DIR", __import__("pathlib").Path(tmp)), \
                mock.patch.object(config, "LOGS", __import__("pathlib").Path(tmp)), \
                mock.patch.dict("os.environ", {"SOURCE_METERLESS": "false"}):
            for rid, date in (("run-a", "2016-12-01"), ("run-b", "2016-12-13")):
                store = world_model.WorldStoreService(rid)
                ingest = world_model.IngestPipelineService(store)
                for d in (date, "1999-01-01"):     # a second write in the same run does not change the first
                    ingest.upsert_source(Document(source_id="s1", type="journal", title="t", text="q", url="",
                                                  pub_date=d, access="abstract_only"))
                ingest.ingest_fact(case_id="x", entity_id="drug:x", key="k", value=1, source_id="s1", quote="q",
                                   quote_verified=True, agent="test")
                self.assertEqual(world_model.ContextBuilderService(store).facts("x")[0]["pub_date"], date)


class PubMedFallback(unittest.TestCase):
    def test_query_syntax_and_dates_are_translated(self):
        from app.tools import pubmed
        self.assertEqual(pubmed.to_pubmed_query('"X-1" AND (TITLE:design OR TITLE:"baseline characteristics") AND PUB_TYPE:"Randomized Controlled Trial"'),
                         '"X-1" AND (design[ti] OR "baseline characteristics"[ti]) AND "Randomized Controlled Trial"[pt]')
        self.assertEqual(pubmed.to_pubmed_query("EXT_ID:123 OR PMCID:PMC9"), "123[pmid] OR PMC9[pmc]")
        self.assertEqual([pubmed._date(x) for x in ("2016 Apr 2", "2016 Apr", "2016")], ["2016-04-02", "2016-04-01", "2016-01-01"])

    def test_an_outage_switches_to_pubmed_and_a_404_does_not(self):
        from unittest import mock
        from app.tools import europepmc, http
        europepmc._OUTAGE["until"] = 0
        with mock.patch.object(europepmc, "_search", side_effect=http.SourceError("Europe PMC failed after retries: Europe PMC HTTP 503")), \
                mock.patch("app.tools.pubmed.search", return_value=[{"pmid": "1"}]) as pm:
            self.assertEqual(europepmc.search("x"), [{"pmid": "1"}])
            pm.assert_called_once()
        europepmc._OUTAGE["until"] = 0
        with mock.patch.object(europepmc, "_search", side_effect=http.SourceError("Europe PMC HTTP 404: no such page")):
            with self.assertRaises(http.SourceError):
                europepmc.search("x")
