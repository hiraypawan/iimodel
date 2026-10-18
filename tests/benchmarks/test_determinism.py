"""Benchmark: the same query produces the same structure every time.

The check compares the *structure* of two runs (keys, hop ids, statuses,
orders) and ignores live-data fields (timestamps, latency, ordering of
network-dependent sets).  The fetcher is injected, so the test is offline and
deterministic.
"""

from __future__ import annotations

import json
import unittest

from reasoning.logic_tree_solver import LogicTreeSolver
from tests.benchmarks.support import page

PAGES = {
    "France capital": [
        page(
            "https://example.org/france",
            "France",
            [
                "Paris is the capital and most populous city of France.",
                "France is a country in Western Europe with several overseas regions and territories.",
            ],
        ),
        page(
            "https://example.net/france",
            "France facts",
            [
                "The capital city of France is Paris, with about 2.1 million residents in the city proper.",
            ],
        ),
    ],
    "default": [
        page(
            "https://example.org/other",
            "Other",
            ["This page mentions Paris in passing for unrelated reasons."],
        ),
    ],
}


def _fetcher(query: str, max_results: int):
    for needle, pages in PAGES.items():
        if needle != "default" and needle.lower() in (query or "").lower():
            return pages[:max_results]
    return PAGES["default"][:max_results]


def _structure(payload: dict) -> str:
    """Return the stable structure of a solver result."""
    trimmed = {
        "status": payload.get("status"),
        "query": payload.get("query"),
        "hop_count": payload.get("hop_count"),
        "execution_order": payload.get("execution_order"),
        "steps": [
            {
                "id": step.get("id"),
                "hop_type": step.get("hop_type"),
                "query_text": step.get("query_text"),
                "depends_on": step.get("depends_on"),
                "status": step.get("status"),
                "answer": step.get("answer"),
                "source_urls": [
                    passage.get("url") for passage in step.get("passages", [])
                ],
            }
            for step in payload.get("steps", [])
        ],
        "source_urls": [source.get("url") for source in payload.get("sources", [])],
    }
    return json.dumps(trimmed, sort_keys=True, ensure_ascii=False)


class DeterminismTests(unittest.TestCase):
    def test_same_query_yields_identical_structure(self):
        solver = LogicTreeSolver(fetcher=_fetcher)
        first = solver.solve("What is the capital of France?")
        second = solver.solve("What is the capital of France?")
        self.assertEqual(_structure(first), _structure(second))

    def test_decomposition_is_stable_across_calls(self):
        solver = LogicTreeSolver(fetcher=_fetcher)
        first = solver.solve("Who is the CEO of the company that created WhatsApp?")
        second = solver.solve("Who is the CEO of the company that created WhatsApp?")
        self.assertEqual(_structure(first), _structure(second))

    def test_hypothesis_status_is_stable(self):
        from invention.combinatorial_hypothesis_engine import (
            CombinatorialHypothesisEngine,
        )

        def fetcher(query: str, max_results: int = 5):
            return [
                {
                    "title": "Titanium alloy coating",
                    "url": "https://example.org/ti",
                    "snippet": "Titanium alloy is used in coatings.",
                    "authority_score": 70,
                }
            ][:max_results]

        engine = CombinatorialHypothesisEngine(
            llm=None,
            fetcher=fetcher,
            material_exists=lambda material: True,
            sandbox_runner=lambda code, timeout: (True, "ok"),
        )
        first = engine.test_hypotheses("reduce fouling")
        second = engine.test_hypotheses("reduce fouling")
        self.assertEqual(first["status"], second["status"])
        self.assertEqual(first.get("hypothesis"), second.get("hypothesis"))
        self.assertEqual(first.get("assumptions"), second.get("assumptions"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
