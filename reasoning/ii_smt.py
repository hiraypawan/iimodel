"""
Optional Z3-backed solver for formally specified constraints.

The solver checks the formula it is given; it does not translate natural-language
questions into constraints or verify that a user/model encoded the original
problem correctly. Install z3-solver separately to enable this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from reasoning.constraint_compiler import ConstraintCompiler


@dataclass
class SMTResult:
    satisfiable: bool
    model: dict[str, str] = field(default_factory=dict)
    contradiction: Optional[str] = None
    detail: str = ""


class Z3Solver:
    """
    Optional wrapper around Z3 for integer, real, and Boolean constraints.
    It checks the supplied formula; it does not establish that the formula
    correctly represents a natural-language problem.

    Usage:
        solver = Z3Solver()
        result = solver.solve_constraints(
            variables=[("x", "Int"), ("y", "Int")],
            constraints=["x > 0", "y > 0", "x + y == 10", "x * y == 21"],
        )
        # result.satisfiable == True, result.model == {"x": "3", "y": "7"}
    """

    def __init__(self):
        try:
            import z3  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "z3-solver is required for formal constraint solving. "
                "Install it: pip install z3-solver\n"
                f"Original error: {exc}"
            )
        self.z3 = z3

    def solve_constraints(
        self,
        variables: list[tuple[str, str]],
        constraints: list[str],
    ) -> SMTResult:
        """
        Solve a set of logical constraints over integer/real variables.

        variables: [("x", "Int"), ("y", "Real"), ...]
        constraints: supported explicit formulas such as ["x > 0", "x + y == 10"];
        expressions are validated and compiled from a restricted AST.
        """
        try:
            solver = self.z3.Solver()
            z3_vars: dict[str, object] = {}

            # Declare variables
            for name, vtype in variables:
                if name in z3_vars:
                    raise ValueError(f"Duplicate variable declaration: {name!r}.")
                if vtype == "Int":
                    z3_vars[name] = self.z3.Int(name)
                elif vtype == "Real":
                    z3_vars[name] = self.z3.Real(name)
                elif vtype == "Bool":
                    z3_vars[name] = self.z3.Bool(name)
                else:
                    raise ValueError(f"Unsupported variable type {vtype!r} for {name!r}.")

            # Validate and compile from a restricted AST; never eval user text.
            compiler = ConstraintCompiler()
            for constraint in constraints:
                expr = compiler.compile_expression(constraint, z3_vars, self.z3)
                solver.add(expr)

            check = solver.check()

            if check == self.z3.sat:
                model = solver.model()
                model_dict = {}
                for d in model.decls():
                    model_dict[d.name()] = str(model[d])
                return SMTResult(
                    satisfiable=True,
                    model=model_dict,
                    detail="SMT solver found a satisfying assignment.",
                )

            if check == self.z3.unsat:
                return SMTResult(
                    satisfiable=False,
                    contradiction="Constraints are unsatisfiable — no solution exists.",
                    detail="Z3 returned 'unsat'. The problem has no solution under the given constraints.",
                )

            return SMTResult(
                satisfiable=False,
                detail=f"Z3 returned unknown status: {check}",
            )

        except Exception as exc:  # pragma: no cover - z3 runtime errors
            return SMTResult(
                satisfiable=False,
                detail=f"Solver error: {exc}",
            )

    def verify_proof_step(
        self,
        premise: str,
        conclusion: str,
        variables: list[tuple[str, str]],
    ) -> SMTResult:
        """Search for a counterexample to ``premise => conclusion``.

        A satisfiable result means the supplied premise and negated conclusion
        are jointly satisfiable, so a counterexample exists. An unsatisfiable
        result means no counterexample was found under this formula. Callers
        must inspect ``detail`` because solver-unknown and errors are also
        represented as ``satisfiable=False`` without a contradiction.
        """
        # To prove P => C, we check if P AND NOT C is unsat.
        negated = f"({conclusion}) == False" if "==" in conclusion else f"not ({conclusion})"
        all_constraints = [premise, negated]
        return self.solve_constraints(variables, all_constraints)


def smt_math_tool(problem_description: str) -> str:
    """Parse explicit VARIABLES/CONSTRAINTS lines and pass them to Z3.

    This helper does not infer a formalization from free-form natural language.
    Correctness of the modelled constraints remains the caller's responsibility.
    """
    try:
        solver = Z3Solver()
    except ImportError as exc:
        return f"Z3 solver not available: {exc}"

    try:
        formal_problem = ConstraintCompiler().parse_problem(problem_description)
    except ValueError as exc:
        return (
            f"Invalid explicit constraints: {exc}\n"
            "Expected format: VARIABLES: x Int, y Int\n"
            "CONSTRAINTS: x > 0, y > 0, x + y == 10"
        )

    variables = list(formal_problem.variables.items())
    result = solver.solve_constraints(variables, list(formal_problem.constraints))

    if result.satisfiable:
        model_str = ", ".join(f"{k}={v}" for k, v in result.model.items())
        return f"SATISFIABLE. Solution: {model_str}\n{result.detail}"
    if result.contradiction:
        return f"UNSATISFIABLE. {result.contradiction}\n{result.detail}"
    return f"SOLVER ERROR OR UNKNOWN. {result.detail}"


if __name__ == "__main__":
    # Self-test
    solver = Z3Solver()
    r = solver.solve_constraints(
        variables=[("x", "Int"), ("y", "Int")],
        constraints=["x > 0", "y > 0", "x + y == 10", "x * y == 21"],
    )
    print("SMT Self-test:", r)
