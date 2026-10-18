from __future__ import annotations

import unittest
from pathlib import Path

from tests.benchmarks.eval_runner import (
    build_comparison_packet,
    build_review_packet,
    load_tasks,
)


class EvaluationHarnessTests(unittest.TestCase):
    def test_empty_checked_in_dataset_is_valid_and_yields_no_score(self):
        tasks = load_tasks(Path(__file__).parent / "benchmarks" / "eval_questions.json")
        packet = build_review_packet(
            tasks, {}, system="II", model="not-run", settings="not-run"
        )
        self.assertEqual(packet["results"], [])
        self.assertTrue(packet["human_review_required"])
        self.assertTrue(packet["mechanical_signals_only"])

    def test_packet_reports_only_mechanical_citation_signals(self):
        task = {
            "id": "sample-1",
            "question": "Check a claim.",
            "category": "web",
            "answerable": True,
            "gold_sources": ["https://example.org/source"],
            "reference_claims": ["Reference claim."],
        }
        packet = build_review_packet(
            [task],
            {"sample-1": "Claim text. https://example.org/source"},
            system="II",
            model="mock",
            settings="temperature=0",
        )
        result = packet["results"][0]
        self.assertTrue(result["citation_present"])
        self.assertTrue(result["gold_source_url_matched"])
        self.assertIsNone(result["manual_review"]["factual_correctness"])
        self.assertEqual(result["manual_review"]["status"], "pending")

    def test_comparison_packet_keeps_raw_and_pipeline_review_human(self):
        task = {
            "id": "paired-1",
            "question": "Check a claim.",
            "category": "research",
            "answerable": True,
            "gold_sources": ["https://example.org/source"],
            "reference_claims": ["A reviewed claim."],
            "ground_truth": "A reviewed answer.",
            "difficulty": "medium",
        }
        packet = build_comparison_packet(
            [task],
            {"paired-1": "Raw answer."},
            {"paired-1": "II answer. https://example.org/source"},
            raw_model="Qwen local",
            ii_model="Qwen local",
            settings="matched settings",
        )
        result = packet["results"][0]
        self.assertTrue(packet["human_review_required"])
        self.assertIsNone(packet["automatic_quality_score"])
        self.assertEqual(result["manual_comparison"]["status"], "pending")
        self.assertFalse(result["raw_model"]["citation_present"])
        self.assertTrue(result["ii_v2"]["citation_present"])

    def test_abstention_phrase_detection_is_not_marked_as_correctness(self):
        task = {
            "id": "sample-2",
            "question": "Unknown fact?",
            "category": "web",
            "answerable": False,
            "gold_sources": [],
            "reference_claims": [],
        }
        packet = build_review_packet(
            [task],
            {"sample-2": "Insufficient evidence to answer."},
            system="II",
            model="mock",
            settings="unspecified",
        )
        result = packet["results"][0]
        self.assertTrue(result["abstention_phrase_detected"])
        self.assertIsNone(result["manual_review"]["abstention_appropriate"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
