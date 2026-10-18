"""CLI contract tests for verification input and machine-readable output."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"


class VerifyCliTests(unittest.TestCase):
    def run_verify(self, *args: str, source: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(MAIN), "verify", *args],
            input=source,
            text=True,
            capture_output=True,
            cwd=ROOT,
            check=False,
        )

    def test_reads_multiline_source_from_stdin_until_eof(self):
        source = "def verify_me():\n    return 7\n"
        result = self.run_verify(source=source)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(json.loads(result.stdout)["passed"])

    def test_reads_source_from_input_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "module.py"
            path.write_text("class Verified:\n    pass\n", encoding="utf-8")
            result = self.run_verify("--input", str(path))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["passed"])

    def test_returns_json_and_nonzero_for_syntax_failure(self):
        result = self.run_verify(source="def broken(:\n    pass\n")

        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertFalse(report["passed"])
        self.assertEqual(report["checks"][0]["name"], "syntax")
        self.assertEqual(report["checks"][0]["status"], "fail")


if __name__ == "__main__":
    unittest.main()
