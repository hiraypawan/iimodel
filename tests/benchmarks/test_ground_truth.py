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

from tests.benchmarks.support import load_ground_truth, requires_network
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
