from __future__ import annotations

import importlib.util
import unittest

from reasoning.constraint_compiler import ConstraintCompiler


class ConstraintCompilerTests(unittest.TestCase):
    def setUp(self):
        self.compiler = ConstraintCompiler()

    def test_parses_explicit_problem_and_parenthesized_constraints(self):
        problem = self.compiler.parse_problem(
            "VARIABLES: x Int, y Real\n"
            "CONSTRAINTS: x > 0, (x + 1) * 2 <= 20, y >= 1.5"
        )
        self.assertEqual(problem.variables, {"x": "Int", "y": "Real"})
        self.assertEqual(
            problem.constraints,
            ("x > 0", "(x + 1) * 2 <= 20", "y >= 1.5"),
        )

    def test_rejects_missing_and_undeclared_variables(self):
        with self.assertRaisesRegex(ValueError, "Missing VARIABLES"):
            self.compiler.parse_problem("CONSTRAINTS: x > 0")
        with self.assertRaisesRegex(ValueError, "Undeclared variable"):
            self.compiler.parse_problem("VARIABLES: x Int\nCONSTRAINTS: y > 0")

    def test_rejects_python_calls_and_attribute_access(self):
        for expression in ('__import__("os")', "x.real", "x[0]", "x ^ 2"):
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    self.compiler.validate_expression(expression, {"x": "Int"})

    def test_rejects_invalid_and_duplicate_declarations(self):
        with self.assertRaisesRegex(ValueError, "Invalid variable declaration"):
            self.compiler.parse_problem("VARIABLES: x String\nCONSTRAINTS: x > 0")
        with self.assertRaisesRegex(ValueError, "Duplicate variable"):
            self.compiler.parse_problem("VARIABLES: x Int, x Real\nCONSTRAINTS: x > 0")

    def test_expression_limits_are_enforced(self):
        with self.assertRaisesRegex(ValueError, "1024 characters"):
            self.compiler.validate_expression("1 + " + "1 + " * 300 + "1", {})

    def test_prose_is_translated_sequentially_only_via_injected_adapter(self):
        problem = "- x is positive.\n- x is less than ten."
        clauses = self.compiler.decompose_problem(problem)
        self.assertEqual(clauses, ["x is positive.", "x is less than ten."])
        seen = []

        def translator(clause):
            seen.append(clause)
            return "x > 0" if "positive" in clause else "x < 10"

        compiled = self.compiler.compile_all(
            problem, variables={"x": "Int"}, translator=translator
        )
        self.assertEqual(compiled, ["x > 0", "x < 10"])
        self.assertEqual(seen, clauses)
        with self.assertRaises(NotImplementedError):
            self.compiler.translate_constraint("x is positive", {"x": "Int"})

    @unittest.skipUnless(importlib.util.find_spec("z3"), "z3-solver is optional")
    def test_z3_uses_restricted_compiler_for_explicit_constraints(self):
        from ii_smt import Z3Solver

        result = Z3Solver().solve_constraints(
            [("x", "Int"), ("y", "Int")],
            ["x > 0", "y > 0", "x + y == 10", "x * y == 21"],
        )
        self.assertTrue(result.satisfiable)
        self.assertEqual(set(result.model.values()), {"3", "7"})

    @unittest.skipUnless(importlib.util.find_spec("z3"), "z3-solver is optional")
    def test_proof_step_result_means_counterexample_exists(self):
        from ii_smt import Z3Solver

        solver = Z3Solver()
        variables = [("x", "Int")]
        valid = solver.verify_proof_step("x > 0", "x > -1", variables)
        invalid = solver.verify_proof_step("x > 0", "x > 1", variables)
        self.assertFalse(valid.satisfiable)
        self.assertIsNotNone(valid.contradiction)
        self.assertTrue(invalid.satisfiable)


if __name__ == "__main__":
    unittest.main(verbosity=2)
