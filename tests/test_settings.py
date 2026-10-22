import os
import tempfile
import unittest
from pathlib import Path

from app import config, settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_path, self.saved_env = settings.ENV_PATH, dict(os.environ)
        settings.ENV_PATH = Path(self.tmp.name) / ".env"
        settings.ENV_PATH.write_text("# comment kept\nPORT=8000\n")

    def tearDown(self):
        settings.ENV_PATH = self.saved_path
        os.environ.clear()
        os.environ.update(self.saved_env)
        self.tmp.cleanup()

    def test_key_is_saved_but_never_returned(self):
        fake = "sk-ant-test-" + "x" * 30 + "ABCD"
        result = settings.update({"providers": {"anthropic": {"key": fake}}})
        anthropic = next(p for p in result["providers"] if p["id"] == "anthropic")
        self.assertTrue(anthropic["configured"])
        self.assertEqual(anthropic["key_hint"], "•••• ABCD")
        self.assertNotIn(fake, str(result))
        text = settings.ENV_PATH.read_text()
        self.assertIn("# comment kept", text)
        self.assertIn(f"ANTHROPIC_API_KEY={fake}", text)

    def test_clear_key(self):
        settings.update({"providers": {"anthropic": {"key": "sk-ant-" + "y" * 30}}})
        settings.update({"providers": {"anthropic": {"clear_key": True}}})
        self.assertIsNone(config.provider_key("anthropic"))
        self.assertNotIn("ANTHROPIC_API_KEY", settings.ENV_PATH.read_text())

    def test_bad_values_are_rejected(self):
        with self.assertRaises(ValueError):
            settings.update({"providers": {"anthropic": {"key": "bad key with spaces"}}})
        with self.assertRaises(ValueError):
            settings.update({"roles": {"reasoning": {"provider": "nope", "model": "x"}}})
        with self.assertRaises(ValueError):
            settings.update({"run": {"CONTACT_EMAIL": "not-an-email"}})

    def test_sources_and_run_options(self):
        settings.update({"sources": {"europepmc": False, "ctgov": False}, "run": {"MAX_LITERATURE_DOCS": 99}})
        self.assertFalse(config.source_enabled("europepmc"))
        self.assertTrue(config.source_enabled("ctgov"))          # required source cannot be switched off
        self.assertEqual(config.max_literature_docs(), 15)        # clamped to the allowed range


class AgentModelTests(SettingsTests):
    def test_per_agent_model_overrides_group_without_changing_replay_key(self):
        from app.llm.generator import request_key
        key_before = request_key("reasoning", "s", "p")
        settings.update({"agents": {"Investigator": {"provider": "anthropic", "model": "claude-sonnet-5-5"}}})
        self.assertEqual(config.agent_spec("Investigator", "reasoning").model, "claude-sonnet-5-5")
        self.assertEqual(config.agent_spec("MechanismAnalyst", "reasoning").model, config.role("reasoning").model)
        self.assertEqual(request_key("reasoning", "s", "p"), key_before)

    def test_apply_recommended_and_reset(self):
        settings.update({"apply_recommended": True})
        agents = {a["name"]: a for a in settings.read()["agents"] if a["kind"] == "ai"}
        self.assertTrue(all(a["is_recommended"] for a in agents.values()))
        settings.update({"agents": {"Reporter": {"reset": True}}})
        self.assertFalse(next(a for a in settings.read()["agents"] if a["name"] == "Reporter")["own_setting"])

    def test_zetaris_connection_settings(self):
        for key in ("ZETARIS_API_KEY", "ZETARIS_ORG_ID", "ZETARIS_REST_URL"):
            os.environ.pop(key, None)
        self.assertFalse(settings.read()["zetaris"]["configured"])
        with self.assertRaises(ValueError):
            settings.update({"zetaris": {"org": "my-org"}})
        with self.assertRaises(ValueError):
            settings.update({"zetaris": {"url": "http://localhost:3000"}})
        data = settings.update({"zetaris": {"url": "http://localhost:8888/api/v1.0", "org": "1", "key": "zt-test-key-123456"}})
        self.assertTrue(data["zetaris"]["configured"])
        self.assertNotIn("zt-test-key-123456", str(data))
        self.assertIn("ZETARIS_API_KEY=zt-test-key-123456", settings.ENV_PATH.read_text())
        os.environ["SAMPLE_MODE"] = "true"
        self.assertFalse(settings.read()["zetaris"]["active"])  # offline mode never calls Zetaris
        settings.update({"zetaris": {"clear_key": True}})
        self.assertFalse(settings.read()["zetaris"]["configured"])

    def test_zetaris_card_saves_through_the_api(self):
        """The Settings page posts to /api/settings; the request model must keep the zetaris block."""
        from fastapi.testclient import TestClient
        from app.main import app
        for key in ("ZETARIS_API_KEY", "ZETARIS_ORG_ID", "ZETARIS_REST_URL"):
            os.environ.pop(key, None)
        client = TestClient(app)
        r = client.post("/api/settings", json={"zetaris": {"url": "http://localhost:8888/api/v1.0", "org": "1",
                                                           "key": "zt-api-key-7890"}})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["zetaris"]["configured"])
        self.assertIn("ZETARIS_API_KEY=zt-api-key-7890", settings.ENV_PATH.read_text())
        r = client.post("/api/settings", json={"zetaris": {"clear_key": True}})
        self.assertFalse(r.json()["zetaris"]["configured"])
        self.assertNotIn("ZETARIS_API_KEY", settings.ENV_PATH.read_text())

    def test_nvidia_always_has_its_own_address(self):
        os.environ.pop("NVIDIA_BASE_URL", None)
        self.assertEqual(config.base_url("nvidia"), "https://integrate.api.nvidia.com/v1")
        os.environ["NVIDIA_BASE_URL"] = "  "
        self.assertEqual(config.base_url("nvidia"), "https://integrate.api.nvidia.com/v1")
        os.environ["NVIDIA_BASE_URL"] = "http://dgx-spark.local:8000/v1"
        self.assertEqual(config.base_url("nvidia"), "http://dgx-spark.local:8000/v1")


if __name__ == "__main__":
    unittest.main()
