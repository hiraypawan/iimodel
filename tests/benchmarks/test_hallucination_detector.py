"""Benchmark: the hallucination detector catches injected entities.

The detector is an **entity diff** between the rendered text and the JSON it
was rendered from.  These tests check that synthetic injections are caught.

The known blind spots (relation flips, negation/qualifier changes, paraphrase
false positives) are **documented, not asserted**: the test only checks that
the blind spots are stated in the code, so that nobody mistakes the detector
for a complete guarantee.
"""

from __future__ import annotations

import json
import unittest

from core.caged_renderer import (
    BLIND_SPOTS,
    STRICT_SYSTEM_PROMPT,
    CagedRenderer,
    check_llm_output,
)
from tests.benchmarks.support import FakeLLM

SOURCE_JSON = {
    "status": "VERIFIED",
    "final_answer": "Answer: Paris\nSources:\n[1] France -- https://example.org/france",
    "sources": [
        {
            "url": "https://example.org/france",
            "title": "France",
            "quote": "Paris is the capital and most populous city of France.",
        }
    ],
    "population": "67000000",
}


class DetectorTests(unittest.TestCase):
    def test_clean_rendering_passes(self):
        text = "Paris is the capital of France. The population is 67000000."
        verdict = check_llm_output(text, SOURCE_JSON)
        self.assertTrue(verdict["ok"], verdict["added_entities"])
        self.assertEqual(verdict["added_entities"], [])

    def test_injected_number_is_caught(self):
        text = "Paris is the capital of France. The population is 99000000."
        verdict = check_llm_output(text, SOURCE_JSON)
        self.assertFalse(verdict["ok"])
        self.assertTrue(
            any("99000000" in entity.replace(",", "") for entity in verdict["added_entities"]),
            verdict["added_entities"],
        )

    def test_injected_name_is_caught(self):
        text = "Paris is the capital of France, according to Napoleon Bonaparte."
        verdict = check_llm_output(text, SOURCE_JSON)
        self.assertFalse(verdict["ok"])
        self.assertTrue(
            any("Napoleon" in entity for entity in verdict["added_entities"]),
            verdict["added_entities"],
        )

    def test_injected_url_is_caught(self):
        text = "Paris is the capital of France. See https://evil.example.net/paper"
        verdict = check_llm_output(text, SOURCE_JSON)
        self.assertFalse(verdict["ok"])

    def test_injected_date_is_caught(self):
        text = "Paris is the capital of France as of 2026-04-01."
        verdict = check_llm_output(text, SOURCE_JSON)
        self.assertFalse(verdict["ok"])

    def test_number_format_variants_are_treated_as_equal(self):
        text = "The population is 67,000,000."
        verdict = check_llm_output(text, SOURCE_JSON)
        self.assertTrue(verdict["ok"], verdict["added_entities"])

    def test_result_reports_the_documented_blind_spots(self):
        verdict = check_llm_output("Paris is the capital of France.", SOURCE_JSON)
        self.assertIn("note", verdict)
        self.assertIn("relation flips", verdict["note"])
        self.assertEqual(list(verdict["blind_spots"]), list(BLIND_SPOTS))

    def test_blind_spots_are_documented_in_the_source(self):
        import inspect

        import core.caged_renderer as module

        source = inspect.getsource(module)
        for marker in ("relation flip", "negation", "paraphrase"):
            self.assertIn(marker, source.lower())


class RendererTests(unittest.TestCase):
    def test_strict_system_prompt_is_exact(self):
        self.assertEqual(
            STRICT_SYSTEM_PROMPT,
            "YOU ARE A DETERMINISTIC TEXT RENDERER. Convert the provided JSON "
            "into natural, fluent Hinglish/English. ZERO HALLUCINATION: You "
            "MUST NOT add any facts, numbers, or names not explicitly present "
            "in the JSON. Stick strictly to the schema.",
        )

    def test_rejected_rendering_is_discarded_and_retried(self):
        llm = FakeLLM(responses=[
            "Paris is the capital. The population is 99000000.",
            "Paris is the capital of France. The population is 67000000.",
        ])
        renderer = CagedRenderer(llm=llm)
        text = renderer.render(SOURCE_JSON)
        self.assertIn("67000000", text)
        self.assertNotIn("99000000", text)
        self.assertTrue(renderer.rejections)
        self.assertEqual(renderer.rejections[0]["added_entities"], ["99000000"])

    def test_persistent_rejection_falls_back_to_the_raw_json(self):
        llm = FakeLLM(responses=[
            "Napoleon Bonaparte ruled from Paris.",
            "Marie Curie was born in Paris.",
        ])
        renderer = CagedRenderer(llm=llm)
        text = renderer.render(SOURCE_JSON)
        self.assertIn("rejected by the hallucination detector", text)
        self.assertIn("final_answer", text)
        self.assertEqual(len(renderer.rejections), 2)

    def test_renderer_without_a_model_is_deterministic(self):
        renderer = CagedRenderer(llm=None)
        first = renderer.render(SOURCE_JSON)
        second = renderer.render(SOURCE_JSON)
        self.assertEqual(first, second)
        self.assertIn("Paris", first)

    def test_demo_backend_is_not_used_as_a_renderer(self):
        """The bundled demo placeholder must not pose as a renderer.

        It returns canned sentences, so the entity diff would reject them and
        every answer would degrade into a raw JSON dump.  Without a real
        generative backend the deterministic renderer is used instead.
        """

        class DemoBackend:
            mode = "demo"

            def chat(self, *args, **kwargs):  # pragma: no cover - never called
                raise AssertionError("the demo backend must not be used as a renderer")

        renderer = CagedRenderer(llm=DemoBackend())
        text = renderer.render(SOURCE_JSON)
        self.assertIn("Status: VERIFIED", text)
        self.assertIn("Paris", text)
        self.assertNotIn("rejected by the hallucination detector", text)
        self.assertEqual(renderer.rejections, [])

    def test_renderer_only_sends_json_to_the_model(self):
        llm = FakeLLM(responses=["Paris is the capital of France."])
        renderer = CagedRenderer(llm=llm)
        renderer.render(SOURCE_JSON)
        self.assertTrue(llm.calls)
        system_message = llm.calls[0][0]
        self.assertEqual(system_message["role"], "system")
        self.assertEqual(system_message["content"], STRICT_SYSTEM_PROMPT)
        payload = json.loads(llm.calls[0][1]["content"].split("Render this JSON as natural text:\n", 1)[1])
        self.assertEqual(payload["status"], "VERIFIED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
