"""Benchmark: the external ground-truth question set.

This test uses ground truth that lives **outside** the project
(``qa_ground_truth.json`` -- stable public facts written by hand).  For every
question it asserts:

* the solver reports ``VERIFIED``,
* the expected answer substring is present,
* at least two source URLs are returned,
* every returned URL resolves with HTTP 200,
* at least the required number of expected source domains appear.

The test is skipped (not failed) when the sandbox has no internet access.
"""

from __future__ import annotations

import unittest
from urllib.parse import urlparse

from tests.benchmarks.support import (
    load_ground_truth,
    offline_fetcher,
    page,
    requires_network,
)
from reasoning.logic_tree_solver import LogicTreeSolver


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _matches_domain(host: str, expected: str) -> bool:
    expected = expected.lower()
    return host == expected or host.endswith("." + expected)


def _http_status(url: str, timeout: int = 15) -> int:
    import requests

    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "II-benchmark/3.0 (+citation check)"},
            allow_redirects=True,
        )
        return response.status_code
    except Exception:
        return 0


class GroundTruthBenchmark(unittest.TestCase):
    """The 20-question external ground-truth set."""

    def test_ground_truth_file_shape(self):
        questions = load_ground_truth()
        self.assertEqual(len(questions), 20, "the ground truth set must hold 20 questions")
        ids = [question["id"] for question in questions]
        self.assertEqual(len(set(ids)), len(ids), "question ids must be unique")
        for question in questions:
            self.assertTrue(question["query"].strip())
            self.assertTrue(question["expected_answer_substrings"])
            self.assertTrue(question["expected_source_domains"])

    @requires_network("live internet access is required to verify citations")
    def test_ground_truth_questions_are_verified_and_cited(self):
        solver = LogicTreeSolver()
        for question in load_ground_truth():
            with self.subTest(question=question["id"]):
                result = solver.solve(question["query"])

                self.assertEqual(
                    result["status"], "VERIFIED",
                    f"{question['id']}: expected VERIFIED, got {result['status']} "
                    f"({result.get('missing')})",
                )

                haystack = " ".join([
                    result.get("final_answer") or "",
                    " ".join(
                        f"{p.get('quote', '')} {p.get('url', '')}"
                        for p in result.get("sources", [])
                    ),
                ]).lower()
                self.assertTrue(
                    any(
                        needle.lower() in haystack
                        for needle in question["expected_answer_substrings"]
                    ),
                    f"{question['id']}: none of "
                    f"{question['expected_answer_substrings']} found in the answer",
                )

                urls = [source["url"] for source in result["sources"] if source.get("url")]
                self.assertGreaterEqual(
                    len(urls), 2,
                    f"{question['id']}: at least two URLs are required",
                )

                hosts = {_domain(url) for url in urls}
                required = int(question.get("min_matching_domains", 1))
                matching = [
                    host for host in hosts
                    if any(
                        _matches_domain(host, expected)
                        for expected in question["expected_source_domains"]
                    )
                ]
                self.assertGreaterEqual(
                    len(matching), required,
                    f"{question['id']}: expected a source from "
                    f"{question['expected_source_domains']}, got {sorted(hosts)}",
                )

                for url in urls:
                    status = _http_status(url)
                    self.assertEqual(
                        status, 200,
                        f"{question['id']}: {url} returned HTTP {status}",
                    )

    @requires_network("live internet access is required to verify citations")
    def test_independent_domains_back_verified_claims(self):
        solver = LogicTreeSolver()
        result = solver.solve("What is the capital of France?")
        if result["status"] != "VERIFIED":
            self.skipTest("the live sources did not reach VERIFIED for this run")
        hosts = {_domain(source["url"]) for source in result["sources"]}
        self.assertGreaterEqual(
            len(hosts), 2,
            "VERIFIED requires two independent source domains",
        )


class OfflineGroundTruthPipeline(unittest.TestCase):
    """The ground-truth checks, offline, against deterministic fixture pages.

    The live tests above skip without internet -- and that skip is exactly
    how answer-extraction regressions stayed invisible: "capital of France"
    extraction used to return the predicate of appositive sentences ("one of
    the world's great cultural centres") under a ``VERIFIED`` stamp, and
    multi-hop questions used to print an intermediate hop's answer on the
    ``Answer:`` line.  These tests run the *real* solver with a stubbed
    fetcher and pin the extraction and attribution layers, so CI catches
    such bugs with no network access at all.
    """

    def _solve(self, query: str, pages_by_needle: dict) -> dict:
        solver = LogicTreeSolver(fetcher=offline_fetcher(pages_by_needle))
        return solver.solve(query)

    def test_capital_question_offline_matches_the_ground_truth(self):
        """q01 over fixtures: the extracted value must be ``Paris``.

        The pages deliberately mix all three surface forms -- including the
        appositive sentence whose *predicate* must never be returned.
        """
        expected = {
            needle.lower()
            for question in load_ground_truth()
            if question["id"] == "q01"
            for needle in question["expected_answer_substrings"]
        }
        self.assertTrue(expected, "q01 must exist in the ground truth set")

        result = self._solve(
            "What is the capital of France?",
            {
                "capital": [
                    page(
                        "https://en.wikipedia.org/wiki/France",
                        "France - Wikipedia",
                        [
                            "Paris is the capital and most populous city of France.",
                            "Paris, the capital of France, is one of the world's "
                            "great cultural centres.",
                        ],
                    ),
                    page(
                        "https://www.britannica.com/place/France",
                        "France | History, Map, & Facts",
                        ["The capital of France is Paris."],
                    ),
                ],
            },
        )

        self.assertEqual(result["status"], "VERIFIED", result.get("missing"))
        answer = (result.get("final_answer") or "").lower()
        self.assertTrue(
            any(needle in answer for needle in expected),
            f"none of {sorted(expected)} found in {result.get('final_answer')!r}",
        )
        self.assertNotIn(
            "cultural centres",
            answer,
            "the predicate of the appositive sentence must never be the answer",
        )

    def test_multi_hop_offline_attributes_the_answer_to_its_hop(self):
        """Bug C pin: the answer line must name the hop that produced it."""
        result = self._solve(
            "Who is the CEO of the company that created WhatsApp?",
            {
                "whatsapp": [
                    page(
                        "https://en.wikipedia.org/wiki/WhatsApp",
                        "WhatsApp - Wikipedia",
                        ["WhatsApp was created by Brian Acton and Jan Koum in 2009."],
                    ),
                    page(
                        "https://www.britannica.com/money/WhatsApp",
                        "WhatsApp | Definition, History, & Facts",
                        ["WhatsApp was created by Brian Acton, an American "
                         "entrepreneur, in 2009."],
                    ),
                ],
                "chief executive": [
                    page(
                        "https://en.wikipedia.org/wiki/Mark_Zuckerberg",
                        "Mark Zuckerberg - Wikipedia",
                        ["Mark Zuckerberg is an American businessman and the "
                         "chief executive officer of Meta Platforms."],
                    ),
                    page(
                        "https://www.britannica.com/biography/Mark-Zuckerberg",
                        "Mark Zuckerberg | Biography",
                        ["Mark Zuckerberg is the CEO of Meta, the social "
                         "media company."],
                    ),
                ],
            },
        )

        self.assertEqual(result["status"], "VERIFIED", result.get("missing"))
        by_id = {step["id"]: step for step in result["steps"]}
        self.assertEqual(by_id["hop1"]["answer"], "Brian Acton")
        self.assertEqual(by_id["hop2"]["answer"], "Mark Zuckerberg")
        first_line = (result.get("final_answer") or "").splitlines()[0]
        self.assertEqual(first_line, "Answer: Mark Zuckerberg")
        self.assertIn("(from hop 'hop2' -- leadership)", result["final_answer"])
        self.assertIn("hop 'hop1' (creator)", result["final_answer"])

    def test_multi_hop_offline_never_passes_an_intermediate_off_as_the_answer(self):
        """When the final hop fails, the intermediate result is labelled."""
        result = self._solve(
            "Who is the CEO of the company that created WhatsApp?",
            {
                "whatsapp": [
                    page(
                        "https://en.wikipedia.org/wiki/WhatsApp",
                        "WhatsApp - Wikipedia",
                        ["WhatsApp was created by Brian Acton and Jan Koum in 2009."],
                    ),
                    page(
                        "https://www.britannica.com/money/WhatsApp",
                        "WhatsApp | Definition, History, & Facts",
                        ["WhatsApp was created by Brian Acton, an American "
                         "entrepreneur, in 2009."],
                    ),
                ],
                # No leadership pages: the second hop cannot resolve.
            },
        )

        self.assertEqual(result["status"], "PARTIAL")
        final_answer = result.get("final_answer") or ""
        self.assertFalse(
            final_answer.startswith("Answer:"),
            "an intermediate hop's answer must not be shown as 'Answer:'",
        )
        self.assertIn("Intermediate result only", final_answer)
        self.assertIn("Brian Acton", final_answer)
        self.assertIn("hop 'hop2'", final_answer)

    def test_decimal_population_is_not_truncated(self):
        """Bug B bonus pin: '1.4 billion' must never collapse to '1'."""
        result = self._solve(
            "What is the population of India?",
            {
                "population": [
                    page(
                        "https://en.wikipedia.org/wiki/India",
                        "India - Wikipedia",
                        ["The population of India is 1.4 billion."],
                    ),
                    page(
                        "https://www.britannica.com/place/India",
                        "India | History, Map, & Facts",
                        ["The population of India is 1.4 billion people."],
                    ),
                ],
            },
        )
        by_id = {step["id"]: step for step in result["steps"]}
        self.assertEqual(by_id["hop1"]["answer"], "1.4 billion")


if __name__ == "__main__":
    unittest.main(verbosity=2)
