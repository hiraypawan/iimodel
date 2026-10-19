from __future__ import annotations

import unittest
from unittest.mock import patch

from ingestion.ii_web import (
    SearchResponse,
    SearchResult,
    build_evidence_report,
    candidate_contradictions,
    extract_evidence_passages,
)
from reasoning.logic_tree_solver import _default_fetcher


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

    def test_logic_tree_uses_search_snippet_without_refetching_page(self):
        result = SearchResult(
            citation_id="[1]",
            title="France",
            link="https://example.org/france",
            snippet="The capital of France is Paris, its largest metropolitan city.",
            source="test",
            fetched_at="2026-10-19T00:00:00+00:00",
            authority_score=70,
        )
        response = SearchResponse(
            query="France capital",
            results=[result],
            total_found=1,
            backends_used=["test"],
            citation_map={},
        )
        with patch("ingestion.ii_web.search_results", return_value=response), patch(
            "ingestion.ii_web.fetch_page_text"
        ) as fetch_page:
            pages = _default_fetcher("France capital", 5)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["retrieval"], "search_snippet")
        self.assertIn("Paris", pages[0]["text"])
        fetch_page.assert_not_called()


if __name__ == "__main__":
    unittest.main()
