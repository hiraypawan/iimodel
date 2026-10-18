"""Smoke tests for the II v2 CLI surface."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"


class MainCliTests(unittest.TestCase):
    def run_main(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(MAIN), *args],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
        )

    def test_query_can_be_passed_positionally(self):
        result = self.run_main("interview", "Build a post, then publish it on my site")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("constraint_tree", payload)
        self.assertLessEqual(len(payload.get("questions", [])), 2)

    def test_steer_is_archived_and_reports_no_activation(self):
        result = self.run_main("steer", "--action", "list")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("[ARCHIVED]", result.stdout)
        self.assertNotIn("applied", result.stdout)

    def test_steer_apply_fails_closed_without_backend_hook(self):
        result = self.run_main("steer", "--action", "apply", "--vector", "example")

        self.assertEqual(result.returncode, 2)
        self.assertIn("[ARCHIVED]", result.stdout)

    def test_benchmark_cli_writes_human_review_packet_for_empty_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            responses = root / "responses.json"
            output = root / "review.json"
            responses.write_text("[]\n", encoding="utf-8")
            result = self.run_main(
                "benchmark", "--responses", str(responses), "--output", str(output)
            )
            packet = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(packet["results"], [])
        self.assertTrue(packet["human_review_required"])
        self.assertIn("No model inference", result.stdout)

    def test_benchmark_comparison_accepts_distinct_model_labels_without_scoring(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "tasks.json"
            raw_responses = root / "raw.json"
            ii_responses = root / "ii.json"
            output = root / "comparison.json"
            dataset.write_text("[]\n", encoding="utf-8")
            raw_responses.write_text("[]\n", encoding="utf-8")
            ii_responses.write_text("[]\n", encoding="utf-8")
            result = self.run_main(
                "benchmark",
                "--dataset", str(dataset),
                "--raw-responses", str(raw_responses),
                "--ii-responses", str(ii_responses),
                "--raw-model", "raw-fixture",
                "--ii-model", "ii-fixture",
                "--output", str(output),
            )
            packet = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(packet["run"]["raw_model"], "raw-fixture")
        self.assertEqual(packet["run"]["ii_model"], "ii-fixture")
        self.assertIsNone(packet["automatic_quality_score"])

    def test_ingest_rejects_non_github_url_before_network_access(self):
        result = self.run_main("ingest", "--repo", "https://example.com/owner/repo")

        self.assertEqual(result.returncode, 1)
        self.assertIn("Only public https://github.com/OWNER/REPO URLs", result.stdout)


if __name__ == "__main__":
    unittest.main()
