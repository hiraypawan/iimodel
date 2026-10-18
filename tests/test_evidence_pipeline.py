from __future__ import annotations

import unittest

from ii_web import (
    build_evidence_report,
    candidate_contradictions,
    extract_evidence_passages,
)


class EvidencePipelineTests(unittest.TestCase):
    def test_passages_preserve_exact_text_and_source_url(self):
        passages = extract_evidence_passages(
            "The release shipped in March 2025. A second sentence follows.",
            "https://example.org/release",
        )
        self.assertEqual(passages[0]["quote"], "The release shipped in March 2025.")
        self.assertEqual(passages[0]["source_url"], "https://example.org/release")

    def test_report_abstains_when_no_retrieved_passage_matches_query(self):
        passages = extract_evidence_passages("This page discusses gardening.", "https://example.org")
        report = build_evidence_report("Qwen inference backend", [{"evidence_passages": passages}])
        self.assertEqual(report["status"], "insufficient_evidence")
        self.assertFalse(report["factual_correctness_assessed"])
        self.assertTrue(report["human_review_required"])

    def test_conflict_signals_are_labeled_heuristic(self):
        passages = [
            {"source_url": "https://a.example", "quote": "The release version is 2.0."},
            {"source_url": "https://b.example", "quote": "The release version is not 3.0."},
        ]
        signals = candidate_contradictions(passages)
        self.assertEqual(len(signals), 1)
        self.assertIn("human review", signals[0]["note"].lower())


if __name__ == "__main__":
    unittest.main()
