from __future__ import annotations

import unittest

from reasoning.verification_cascade import VerificationCascade


class VerificationCascadeTests(unittest.TestCase):
    def setUp(self):
        self.verifier = VerificationCascade()

    def test_syntax_and_required_symbol_checks(self):
        report = self.verifier.verify_python(
            "def add(a, b):\n    return a + b\n", required_symbols=["add"]
        )
        self.assertTrue(report.passed)
        self.assertFalse(report.execution_performed)
        self.assertFalse(report.sandboxed)
        self.assertEqual([item.status for item in report.checks], ["pass", "pass", "pass"])

    def test_invalid_syntax_fails_without_execution(self):
        report = self.verifier.verify_python("def broken(:\n pass")
        self.assertFalse(report.passed)
        self.assertFalse(report.execution_performed)
        self.assertEqual(report.checks[0].status, "fail")

    def test_risky_construct_is_a_warning_unless_explicitly_forbidden(self):
        code = "value = eval('1 + 1')"
        warning = self.verifier.verify_python(code)
        self.assertTrue(warning.passed)
        self.assertEqual(warning.checks[-1].status, "warn")
        rejected = self.verifier.verify_python(code, forbidden_calls=["eval"])
        self.assertFalse(rejected.passed)
        self.assertEqual(rejected.checks[-1].status, "fail")

    def test_missing_required_symbol_fails(self):
        report = self.verifier.verify_python("x = 1", required_symbols=["main"])
        self.assertFalse(report.passed)
        self.assertIn("main", report.checks[1].detail)

    def test_full_cascade_skips_unconfigured_gates_and_does_not_claim_isolation(self):
        passed, _detail, report = self.verifier.verify("def add(a, b): return a + b")
        self.assertFalse(passed)
        self.assertEqual([gate["status"] for gate in report["gates"]], ["pass", "skipped", "skipped"])
        self.assertFalse(report["sandboxed"])
        self.assertIsNone(report["execution_performed"])

    def test_injected_cascade_adapters_are_called_one_clause_at_a_time(self):
        generated_from = []
        executed = []
        judged = []

        def cases(docstring):
            generated_from.append(docstring)
            return [{"input": [1, 2], "expected": 3}] * 3

        def runner(code, test_cases):
            executed.append((code, len(test_cases)))
            return True, "Injected test runner passed."

        def entailment(step, requirement):
            judged.append((step, requirement))
            return "yes"

        passed, _, report = self.verifier.verify(
            "def add(a, b): return a + b",
            {
                "docstring": "Return the sum of two inputs.",
                "case_generator": cases,
                "sandbox_runner": runner,
                "step": "add returns a sum",
                "requirements": ["The result is the sum."],
                "entailment_fn": entailment,
            },
        )
        self.assertTrue(passed)
        self.assertEqual(generated_from, ["Return the sum of two inputs."])
        self.assertEqual(executed[0][1], 3)
        self.assertEqual(len(judged), 1)
        self.assertFalse(report["sandboxed"])
        self.assertIsNone(report["execution_performed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
