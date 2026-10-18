from __future__ import annotations

import unittest

from ii_constraints import ConstraintSharder


class FormalConstraintIntegrationTests(unittest.TestCase):
    def test_sharder_compiles_explicit_formulas_and_sequential_callbacks(self):
        sharder = ConstraintSharder()
        problem = "VARIABLES: x Int\nCONSTRAINTS: x > 0, x < 10"
        parsed = sharder.compile_formal_problem(problem)
        self.assertEqual(parsed.variables, {"x": "Int"})
        self.assertEqual(list(parsed.constraints), ["x > 0", "x < 10"])

        seen = []
        constraints = sharder.compile_sequential_problem(
            "x is positive. x is bounded.",
            {"x": "Int"},
            lambda clause: seen.append(clause) or ("x > 0" if "positive" in clause else "x < 10"),
        )
        self.assertEqual(constraints, ["x > 0", "x < 10"])
        self.assertEqual(seen, ["x is positive.", "x is bounded."])

    def test_solve_reports_unavailable_z3_or_formal_result(self):
        sharder = ConstraintSharder()
        outcome = sharder.solve_formal_problem("VARIABLES: x Int\nCONSTRAINTS: x > 0")
        self.assertIn("satisfiable", outcome)
        self.assertIn("detail", outcome)


if __name__ == "__main__":
    unittest.main()
