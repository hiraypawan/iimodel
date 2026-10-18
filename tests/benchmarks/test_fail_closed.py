"""Benchmark: the system fails closed -- it never invents an answer.

A nonsense question must return ``INSUFFICIENT_DATA`` with no sources, no
answer, and no fabricated claim.  The same rule applies when the network is
unavailable and when the retrieved pages contain nothing relevant.
"""

from __future__ import annotations

import unittest

from reasoning.logic_tree_solver import LogicTreeSolver
from tests.benchmarks.support import page

_FABRICATION_MARKERS = (
    "i believe",
    "probably",
    "likely that",
    "it is well known",
    "according to my knowledge",
    "as an ai",
    "the answer is 42",
)


class FailClosedTests(unittest.TestCase):
    def test_nonsense_query_returns_insufficient_data(self):
        solver = LogicTreeSolver(fetcher=lambda query, max_results: [])
        result = solver.solve("zqrxv wibble frobnicate glomp")
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertEqual(result["sources"], [])
        self.assertIn(
            "No verified answer",
            result["final_answer"],
            "the honest meta-answer must be shown instead of an answer",
        )
        self.assertTrue(result["missing"], "the missing work must be reported")

    def test_nonsense_query_fabricates_nothing(self):
        solver = LogicTreeSolver(fetcher=lambda query, max_results: [])
        result = solver.solve("zqrxv wibble frobnicate glomp")
        blob = " ".join([
            result.get("final_answer") or "",
            " ".join(step.get("answer") or "" for step in result.get("steps", [])),
        ]).lower()
        for marker in _FABRICATION_MARKERS:
            self.assertNotIn(marker, blob)

    def test_irrelevant_pages_do_not_produce_an_answer(self):
        solver = LogicTreeSolver(fetcher=lambda query, max_results: [
            page(
                "https://example.org/unrelated",
                "Unrelated",
                ["The mitochondrion is the powerhouse of the cell."],
            )
        ])
        result = solver.solve("Who founded the Antikythera mechanism workshop?")
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertEqual(result["sources"], [])

    def test_partial_result_reports_the_missing_steps(self):
        def fetcher(query: str, max_results: int):
            if "founder" in query.lower() or "created" in query.lower():
                return [page(
                    "https://example.org/a",
                    "A",
                    ["The institute was created by a research group in 1998."],
                )]
            return []

        solver = LogicTreeSolver(fetcher=fetcher)
        result = solver.solve("Who is the CEO of the company that created WhatsApp?")
        self.assertNotEqual(result["status"], "VERIFIED")
        self.assertIn(result["status"], {"PARTIAL", "INSUFFICIENT_DATA"})
        self.assertTrue(result["missing"])

    def test_empty_query_is_rejected(self):
        solver = LogicTreeSolver(fetcher=lambda query, max_results: [])
        result = solver.solve("   ")
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertEqual(result["sources"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
