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
