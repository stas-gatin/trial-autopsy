"""Verifier: SQL safety, the no-Zetaris path and how a contradiction reaches the critic."""
import os
import unittest
from unittest import mock

from app.agents import verifier
from app.llm.generator import LLMResponse

REAL = {"registry": "ta_reg", "papers": "ta_pap"}


class SafeSQL(unittest.TestCase):
    def test_select_is_mapped_to_the_session_tables(self):
        out = verifier.safe_sql("SELECT protocolSection.designModule.enrollmentInfo.count FROM registry", REAL)
        self.assertIn("FROM session.ta_reg", out)
        self.assertTrue(out.endswith(f"LIMIT {verifier.MAX_ROWS}"))

    def test_lateral_view_and_string_literals_are_allowed(self):
        sql = ("SELECT r.pmid, regexp_extract(r.abstractText, '([^.]*73\\\\.7[^.]*)', 1) FROM papers "
               "LATERAL VIEW explode(resultList.result) AS r WHERE r.abstractText LIKE '%drop in Lp(a)%' LIMIT 5")
        out = verifier.safe_sql(sql, REAL)
        self.assertIn("FROM session.ta_pap", out)
        self.assertTrue(out.endswith("LIMIT 5"))

    def test_rejects_anything_but_one_select_over_known_tables(self):
        for sql in ("DROP TABLE session.ta_reg",
                    "CREATE REST SESSION TABLE x REQUEST (endpoint \"http://a\")",
                    "SELECT 1 FROM registry; DROP TABLE session.ta_reg",
                    "SELECT * FROM session.ta_reg",
                    "SELECT * FROM other_table",
                    "SELECT * FROM registry -- comment",
                    "WITH x AS (SELECT 1) SELECT * FROM x"):
            with self.subTest(sql=sql):
                with self.assertRaises(verifier.UnsafeSQL):
                    verifier.safe_sql(sql, REAL)

    def test_unsafe_query_never_reaches_zetaris(self):
        with mock.patch.object(verifier.zetaris, "sql") as run:
            res = verifier.run_query("DELETE FROM registry", {"registry": "https://x"}, session=mock.Mock())
        run.assert_not_called()
        self.assertTrue(res["error"].startswith("rejected"))


class FakeContext:
    def __init__(self, facts):
        self._facts = facts

    def facts(self, case_id):
        return self._facts


class FakeTracer:
    def __init__(self):
        self.events = []

    def log(self, agent, action, **kw):
        self.events.append({"agent": agent, "action": action, **kw})


def _state():
    st = mock.Mock()
    st.case_id = "c"
    st.trial = {"nct_id": "NCT04023552", "acronym": "Lp(a)HORIZON"}
    st.trial_label = "Lp(a)HORIZON NCT04023552"
    st.mechanism = {"changes": [{"biomarker": "Lp(a)", "fact_ids": ["f1"]}], "events_fact_ids": ["f2"]}
    st.context = FakeContext([
        {"fact_id": "f1", "key": "biomarker_change", "value": -73.7, "unit": "%", "attrs": {},
         "source_id": "pmid:36529659", "quote": "-73.7%"},
        {"fact_id": "f2", "key": "planned_events", "value": 993, "unit": "events", "attrs": {},
         "source_id": "ctgov:NCT04023552", "quote": "993 events"},
        {"fact_id": "f3", "key": "readout_statement", "value": "failed", "unit": "", "attrs": {},
         "source_id": "user:readout", "quote": "did not meet"},
    ])
    st.tracer = FakeTracer()
    st.auto_issues = []
    st.verification = {}
    return st


class Claims(unittest.TestCase):
    def test_only_numbers_from_papers_and_registry_are_checked(self):
        items = verifier.claims(_state())
        self.assertEqual([c["fact_id"] for c in items], ["f1", "f2"])
        urls = verifier.tables(_state(), items)
        self.assertIn("NCT04023552", urls["registry"])
        self.assertIn("EXT_ID%3A36529659", urls["papers"])


class WithoutZetaris(unittest.TestCase):
    def test_numbers_are_marked_not_verified(self):
        st = _state()
        with mock.patch.object(verifier.zetaris, "enabled", return_value=False), \
                mock.patch.dict(os.environ, {"SAMPLE_MODE": "false"}):
            verifier.run(st)
        self.assertEqual(st.verification["status"], "unavailable")
        self.assertTrue(all(r["status"] == "not_checked" for r in st.verification["results"]))
        self.assertIn("not independently verified", verifier.summary(st))


def _resp():
    return LLMResponse("", "nvidia", "nvidia/nemotron-3-super-120b-a12b", "k", True, 0.0)


class Contradiction(unittest.TestCase):
    def test_contradiction_goes_to_the_critic(self):
        st = _state()
        items = verifier.claims(st)
        results = {"c1": {"sql": "SELECT ...", "rows": [["36529659", "-80% at day 30"]], "error": None},
                   "c2": {"sql": "SELECT ...", "rows": [], "error": None}}
        verdicts = {"results": [{"claim": "c1", "status": "contradicts", "found": "-80% at day 30"},
                                {"claim": "c2", "status": "confirmed", "found": "993"}]}
        with mock.patch.object(verifier.generator, "generate_json", return_value=(verdicts, _resp())):
            verifier._judge(st, items, [], results)
        by_fact = {r["fact_id"]: r["status"] for r in st.verification["results"]}
        self.assertEqual(by_fact["f1"], "contradicts")
        self.assertEqual(by_fact["f2"], "not_found", "no rows came back, so code does not let it count as confirmed")
        self.assertTrue(any("contradicts the source" in i for i in st.auto_issues))
        self.assertEqual(st.tracer.events[-1]["status"], "needs_revision")



class TimeMachine(unittest.TestCase):
    def test_cutoff_rejects_fields_known_only_after_the_readout(self):
        for sql in ("SELECT resultsSection FROM registry",
                    "SELECT protocolSection.designModule.enrollmentInfo.count FROM registry",
                    "SELECT protocolSection.statusModule.overallStatus FROM registry",
                    "SELECT * FROM registry"):
            with self.subTest(sql=sql):
                with self.assertRaises(verifier.UnsafeSQL):
                    verifier.safe_sql(sql, REAL, cutoff=True)
        ok = verifier.safe_sql("SELECT protocolSection.eligibilityModule.eligibilityCriteria FROM registry", REAL, cutoff=True)
        self.assertIn("session.ta_reg", ok)
        # without a cut-off (a live autopsy) posted results may be read
        verifier.safe_sql("SELECT resultsSection FROM registry", REAL)


if __name__ == "__main__":
    unittest.main()


class CutoffGuardHoles(unittest.TestCase):
    """With an evidence cut-off no way round the field list may reach status, results or enrollment."""
    real = {"registry": "t_reg", "papers": "t_pap"}

    def rejected(self, sql, cutoff=True):
        from app.agents.verifier import UnsafeSQL, safe_sql
        with self.assertRaises(UnsafeSQL):
            safe_sql(sql, self.real, cutoff)

    def test_whole_sections_and_fields_hidden_in_strings(self):
        self.rejected("SELECT protocolSection.designModule FROM registry")
        self.rejected("SELECT to_json(protocolSection) FROM registry")
        self.rejected("SELECT protocolSection['statusModule'] FROM registry")
        self.rejected("SELECT get_json_object(x, '$.statusModule') FROM registry")

    def test_other_tables_by_comma_or_quoted_name(self):
        self.rejected("SELECT a FROM registry, session.other_tbl", cutoff=False)
        self.rejected("SELECT a FROM registry UNION SELECT b FROM `secret`", cutoff=False)

    def test_valid_queries_pass(self):
        from app.agents.verifier import safe_sql
        self.assertIn("count(*)", safe_sql("SELECT count(*) FROM papers WHERE pmid = '1'", self.real, True))
        self.assertIn("EXTRACT(year FROM d)", safe_sql("SELECT EXTRACT(year FROM d) FROM papers", self.real, True))
        self.assertIn("session.t_reg", safe_sql("SELECT protocolSection.designModule.phases FROM registry", self.real, True))
