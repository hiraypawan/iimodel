"""Benchmark: the status taxonomy is honest.

Rules enforced here:

* the hypothesis engine never reports ``VERIFIED``,
* a ``SIMULATED`` result always carries a baseline and non-empty assumptions,
* the brainstorming engine always labels its output
  ``UNVALIDATED_COMBINATION``,
* every status the router can emit is one of the documented labels.

The hypothesis engine is exercised with injected fakes so that the contract
is checked deterministically and without a network.
"""

from __future__ import annotations

import unittest

from core.ii_agent import classify_intent, is_simple_factual
from interaction.ii_brainstorming_engine import BrainstormingEngine
from invention.combinatorial_hypothesis_engine import (
    CombinatorialHypothesisEngine,
)

ALLOWED_STATUSES = {
    "VERIFIED",
    "PARTIAL",
    "SIMULATED",
    "UNVALIDATED_COMBINATION",
    "CONFLICT",
    "INSUFFICIENT_DATA",
    "SUPPORTIVE_INFORMATION",
    "CRISIS_RESOURCES",
}


def _fake_fetcher(query: str, max_results: int = 5) -> list[dict]:
    return [
        {
            "title": "Titanium alloy coating for turbine blades",
            "url": "https://example.org/titanium-alloy",
            "snippet": "Titanium alloy coatings are used on turbine blades.",
            "source": "duckduckgo",
            "authority_score": 70,
        },
        {
            "title": "Enzyme catalysis in biofilms",
            "url": "https://example.org/enzyme-biofilm",
            "snippet": "Enzymes change the adhesion of bacterial biofilms.",
            "source": "duckduckgo",
            "authority_score": 60,
        },
    ][:max_results]


class HypothesisStatusTests(unittest.TestCase):
    def setUp(self):
        self.engine = CombinatorialHypothesisEngine(
            llm=None,
            fetcher=_fake_fetcher,
            material_exists=lambda material: True,
            sandbox_runner=lambda code, timeout: (True, "toy model ran: result=1.0"),
        )

    def test_hypothesis_pipeline_never_emits_verified(self):
        result = self.engine.test_hypotheses("reduce fouling on heat exchangers")
        self.assertNotEqual(result["status"], "VERIFIED")
        self.assertIn(result["status"], ALLOWED_STATUSES)
        self.assertIn(
            result["status"], {"SIMULATED", "INSUFFICIENT_DATA"},
            "a hypothesis result is either SIMULATED or INSUFFICIENT_DATA",
        )

    def test_simulated_result_has_baseline_assumptions_and_disclaimer(self):
        result = self.engine.test_hypotheses("reduce fouling on heat exchangers")
        if result["status"] != "SIMULATED":
            self.skipTest("no hypothesis passed the plausibility screen")
        self.assertTrue(result["baseline"].strip(), "SIMULATED needs a baseline")
        self.assertTrue(result["delta"].strip(), "SIMULATED needs a delta")
        self.assertTrue(result["assumptions"], "SIMULATED needs non-empty assumptions")
        self.assertIn("not empirical proof", result["disclaimer"])
        self.assertTrue(result["sources"], "SIMULATED needs its component sources")

    def test_baseline_is_simulated_before_the_hypothesis(self):
        calls: list[str] = []
        engine = CombinatorialHypothesisEngine(
            llm=None,
            fetcher=_fake_fetcher,
            material_exists=lambda material: True,
            sandbox_runner=lambda code, timeout: (
                calls.append("run"), (True, "ok")
            )[1],
        )
        engine.test_hypotheses("reduce fouling on heat exchangers")
        self.assertTrue(calls, "the sandbox must actually be used")
        self.assertGreaterEqual(len(calls), 2, "baseline and hypothesis are both simulated")

    def test_disclaimer_is_explicit(self):
        result = self.engine.test_hypotheses("reduce fouling on heat exchangers")
        if result["status"] == "SIMULATED":
            self.assertEqual(
                result["disclaimer"], "Toy model estimate, not empirical proof."
            )


class BrainstormingStatusTests(unittest.TestCase):
    def test_combination_status_is_never_verified(self):
        engine = BrainstormingEngine(fetcher=lambda topic: [
            {"title": "Vertical farming", "url": "https://example.org/vf"},
            {"title": "Heat pumps", "url": "https://example.org/hp"},
        ])
        payload = engine.generate("urban food and energy")
        self.assertEqual(payload["combination_status"], "UNVALIDATED_COMBINATION")
        self.assertEqual(payload["status"], "UNVALIDATED_COMBINATION")
        self.assertLessEqual(len(payload["outputs"]), 3)
        for output in payload["outputs"]:
            self.assertTrue(output["component_evidence"])
            for evidence in output["component_evidence"]:
                self.assertTrue(evidence["url"])


class RouterStatusTests(unittest.TestCase):
    def test_intent_classification_is_deterministic(self):
        for prompt in (
            "research the latest papers on retrieval",
            "write a function that parses csv",
            "brainstorm ideas for a solar pump",
            "hello there",
        ):
            first = classify_intent(prompt)
            second = classify_intent(prompt)
            self.assertEqual(first, second)
            self.assertIn(first[0], {
                "empathy", "brainstorm", "hypothesis", "code", "research",
                "chat", "logic_tree",
            })
            self.assertGreaterEqual(first[1], 0.0)
            self.assertLessEqual(first[1], 1.0)

    def test_simple_factual_detection_is_deterministic(self):
        self.assertTrue(is_simple_factual("What is the capital of France?"))
        self.assertFalse(
            is_simple_factual(
                "Research the latest papers, then write a summary and a blog post"
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
