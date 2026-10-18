"""Benchmark: the empathy module is crisis-first and always sourced.

Checks:

* a crisis message short-circuits everything else,
* every technique carries a source URL,
* no efficacy percentages are claimed,
* the mandatory closing disclaimer is always present,
* the module never claims to be a therapist or to have feelings.
"""

from __future__ import annotations

import unittest

from interaction.ii_empathy_module import (
    CRISIS_RESOURCES,
    FRAMEWORKS,
    MANDATORY_CLOSING,
    EmpathyModule,
)


class CrisisTests(unittest.TestCase):
    def test_crisis_message_returns_resources_immediately(self):
        module = EmpathyModule()
        payload = module.crisis_response()
        self.assertTrue(payload["crisis"])
        self.assertEqual(payload["status"], "CRISIS_RESOURCES")
        self.assertTrue(payload["resources"])
        self.assertEqual(payload["resources"], CRISIS_RESOURCES)

    def test_crisis_language_is_detected(self):
        module = EmpathyModule()
        self.assertTrue(module.detect_crisis("I do not want to live anymore"))
        self.assertTrue(module.detect_crisis("I have been thinking about suicide"))
        self.assertFalse(module.detect_crisis("My code does not compile"))

    def test_crisis_takes_priority_over_the_normal_flow(self):
        module = EmpathyModule()
        text = "I am so frustrated and I want to kill myself"
        payload = module.crisis_response() if module.detect_crisis(text) else module.support_plan(text)
        self.assertTrue(payload["crisis"])
        self.assertNotIn("techniques", payload)

    def test_rendered_crisis_message_ends_with_the_disclaimer(self):
        module = EmpathyModule()
        text = module.format("I am thinking about suicide")
        self.assertIn(MANDATORY_CLOSING, text)
        self.assertTrue(any("findahelpline" in resource["contact"] for resource in CRISIS_RESOURCES))


class SentimentTests(unittest.TestCase):
    def test_buckets_cover_the_three_families(self):
        buckets = EmpathyModule().sentiment_buckets(
            "I am so frustrated, this is really annoying and I am stuck"
        )
        self.assertEqual(set(buckets), {"frustration", "anxiety", "sadness"})
        self.assertGreater(buckets["frustration"], 0)

    def test_anxiety_and_sadness_are_detected(self):
        module = EmpathyModule()
        self.assertEqual(
            module.primary_bucket(module.sentiment_buckets("I feel very anxious and worried")),
            "anxiety",
        )
        self.assertEqual(
            module.primary_bucket(module.sentiment_buckets("I am so sad and lonely")),
            "sadness",
        )


class FrameworkTests(unittest.TestCase):
    def test_every_technique_has_a_source_url(self):
        for name, framework in FRAMEWORKS.items():
            self.assertTrue(framework["validation"]["source_url"], name)
            for technique in framework["techniques"]:
                self.assertTrue(technique["source_url"], f"{name}: {technique['name']}")
                self.assertTrue(
                    technique["source_url"].startswith("http"),
                    f"{name}: source URLs must be absolute",
                )

    def test_no_efficacy_percentages_are_claimed(self):
        import re

        for name, framework in FRAMEWORKS.items():
            blob = " ".join([
                framework["validation"]["summary"],
                framework["validation"]["name"],
                *[t["summary"] + " " + t["name"] for t in framework["techniques"]],
            ])
            self.assertIsNone(
                re.search(r"\d+\s*%", blob),
                f"{name} must not quote an efficacy percentage",
            )

    def test_support_plan_always_ends_with_the_disclaimer(self):
        module = EmpathyModule()
        plan = module.support_plan("I am frustrated with this build")
        self.assertEqual(plan["closing"], MANDATORY_CLOSING)
        self.assertTrue(plan["sources"])
        self.assertTrue(plan["techniques"])

    def test_module_never_claims_to_be_a_therapist_or_to_have_feelings(self):
        module = EmpathyModule()
        text = module.format("I am frustrated and stressed about this project")
        lowered = text.lower()
        self.assertNotIn("as an ai", lowered)
        self.assertNotIn("i feel", lowered)
        self.assertNotIn("i am sorry", lowered)
        self.assertIn(MANDATORY_CLOSING, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
