"""Parse and compile an explicit constraint DSL; no natural-language inference.

Accepted input has ``VARIABLES:`` and ``CONSTRAINTS:`` sections, for example::

    VARIABLES: x Int, y Int
    CONSTRAINTS: x > 0, y > 0, x + y == 10

Only declared names, numeric/boolean constants, arithmetic, comparisons, and
boolean operators are allowed. This module deliberately does not ask a language
model to translate prose into formulas.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FormalProblem:
    variables: dict[str, str]
    constraints: tuple[str, ...]


class ConstraintCompiler:
    """Validate explicit formulas and optionally validate translated clauses."""

    def decompose_problem(self, problem: str) -> list[str]:
        """Split prose into simple clauses without interpreting their meaning.

        This is a formatting heuristic only. It cannot guarantee that a sentence
        contains one logical requirement or preserve every nuance.
        """
        clauses: list[str] = []
        for raw_line in problem.splitlines():
            line = re.sub(r"^\s*(?:(?:[-*•])|(?:\d+[.)]))\s*", "", raw_line).strip()
            if not line:
                continue
            for sentence in re.split(r"(?<=[.!?])\s+|;\s*", line):
                sentence = sentence.strip()
                if sentence:
                    clauses.append(sentence)
        return clauses

    def translate_constraint(
        self,
        clause: str,
        variables: dict[str, str],
        translator: Any | None = None,
    ) -> str:
        """Translate one clause through a caller-supplied adapter, then validate.

        Natural-language translation is not implemented by this deterministic
        parser. The callback is invoked once for this clause and must return one
        expression in the supported DSL. Its semantic fidelity remains unverified.
        """
        if translator is None:
            raise NotImplementedError(
                "Natural-language translation requires a caller-supplied translator; "
                "the compiler only validates explicit formulas."
            )
        translated = translator(clause)
        if not isinstance(translated, str):
            raise ValueError("Constraint translator must return a string expression.")
        expression = translated.strip()
        if expression.startswith("assert "):
            expression = expression[len("assert "):].strip()
        if not expression:
            raise ValueError("Constraint translator returned an empty expression.")
        self.validate_expression(expression, variables)
        return expression

    def compile_all(
        self,
        problem: str,
        variables: dict[str, str] | None = None,
        translator: Any | None = None,
    ) -> list[str]:
        """Validate explicit formulas or translate prose clauses sequentially."""
        upper = problem.upper()
        if "VARIABLES:" in upper and "CONSTRAINTS:" in upper:
            return list(self.parse_problem(problem).constraints)
        if not variables:
            raise ValueError("Explicit variable declarations are required for prose clauses.")
        clauses = self.decompose_problem(problem)
        if not clauses:
            raise ValueError("No requirement clauses were found.")
        return [
            self.translate_constraint(clause, variables, translator=translator)
            for clause in clauses
        ]

    def solve(
        self,
        constraints: list[str],
        variables: dict[str, str] | list[tuple[str, str]],
    ) -> dict[str, Any]:
        """Solve already translated explicit formulas with optional Z3."""
        declarations = list(variables.items()) if isinstance(variables, dict) else variables
        variable_map = dict(declarations)
        for expression in constraints:
            self.validate_expression(expression, variable_map)
        try:
            from ii_smt import Z3Solver
            result = Z3Solver().solve_constraints(declarations, constraints)
        except ImportError as exc:
            return {"satisfiable": None, "model": {}, "detail": str(exc)}
        return {
            "satisfiable": result.satisfiable,
            "model": result.model,
            "contradiction": result.contradiction,
            "detail": result.detail,
        }

    def parse_problem(self, text: str) -> FormalProblem:
        variables_parts: list[str] = []
        constraint_parts: list[str] = []
        section: str | None = None

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            upper = line.upper()
            if upper.startswith("VARIABLES:"):
                if section == "variables" or variables_parts:
                    raise ValueError("Duplicate VARIABLES section.")
                section = "variables"
                variables_parts.append(line.split(":", 1)[1].strip())
            elif upper.startswith("CONSTRAINTS:"):
                if section == "constraints" or constraint_parts:
                    raise ValueError("Duplicate CONSTRAINTS section.")
                section = "constraints"
                constraint_parts.append(line.split(":", 1)[1].strip())
            elif section == "variables":
                variables_parts.append(line)
            elif section == "constraints":
                constraint_parts.append(line)
            else:
                raise ValueError("Expected VARIABLES: or CONSTRAINTS: section.")

        if not variables_parts:
            raise ValueError("Missing VARIABLES: section.")
        if not constraint_parts:
            raise ValueError("Missing CONSTRAINTS: section.")

        variables: dict[str, str] = {}
        variable_text = ",".join(part for part in variables_parts if part)
        for item in self._split_top_level(variable_text):
            match = re.fullmatch(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s+(Int|Real|Bool)\s*", item)
            if not match:
                raise ValueError(f"Invalid variable declaration: {item!r}.")
            name, kind = match.groups()
            if name in variables:
                raise ValueError(f"Duplicate variable declaration: {name!r}.")
            variables[name] = kind

        constraint_text = ",".join(part for part in constraint_parts if part)
        constraints = tuple(
            part.strip() for part in self._split_top_level(constraint_text)
            if part.strip()
        )
        if not constraints:
            raise ValueError("At least one constraint is required.")
        for expression in constraints:
            self.validate_expression(expression, variables)
        return FormalProblem(variables=variables, constraints=constraints)

    def validate_expression(self, expression: str, variables: dict[str, str]) -> ast.Expression:
        """Parse a bounded expression; reject calls, attributes, and unknown names."""
        if len(expression) > 1024:
            raise ValueError("Constraint expression exceeds 1024 characters.")
        try:
            parsed = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ValueError(f"Invalid constraint syntax: {exc.msg}.") from exc
        if sum(1 for _ in ast.walk(parsed)) > 256:
            raise ValueError("Constraint expression exceeds 256 syntax nodes.")
        self._validate_node(parsed.body, variables)
        return parsed

    def compile_expression(self, expression: str, symbols: dict[str, Any], z3_module: Any) -> Any:
        """Compile a validated expression to Z3 objects without Python ``eval``."""
        parsed = self.validate_expression(expression, {name: "" for name in symbols})
        return self._compile_node(parsed.body, symbols, z3_module)

    def _validate_node(self, node: ast.AST, variables: dict[str, str]) -> None:
        if isinstance(node, ast.Constant):
            if type(node.value) not in (int, float, bool):
                raise ValueError("Only integer, real, and boolean constants are allowed.")
            return
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise ValueError(f"Undeclared variable {node.id!r}.")
            return
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            self._validate_node(node.left, variables)
            self._validate_node(node.right, variables)
            return
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
            self._validate_node(node.operand, variables)
            return
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            for value in node.values:
                self._validate_node(value, variables)
            return
        if isinstance(node, ast.Compare) and all(type(op) in _COMPARE_OPERATORS for op in node.ops):
            self._validate_node(node.left, variables)
            for comparator in node.comparators:
                self._validate_node(comparator, variables)
            return
        raise ValueError(f"Unsupported constraint expression: {type(node).__name__}.")

    def _compile_node(self, node: ast.AST, symbols: dict[str, Any], z3: Any) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return symbols[node.id]
        if isinstance(node, ast.BinOp):
            op = _BINARY_OPERATORS[type(node.op)]
            return op(self._compile_node(node.left, symbols, z3), self._compile_node(node.right, symbols, z3))
        if isinstance(node, ast.UnaryOp):
            value = self._compile_node(node.operand, symbols, z3)
            if isinstance(node.op, ast.Not):
                return z3.Not(value)
            return _UNARY_OPERATORS[type(node.op)](value)
        if isinstance(node, ast.BoolOp):
            values = [self._compile_node(item, symbols, z3) for item in node.values]
            return z3.And(*values) if isinstance(node.op, ast.And) else z3.Or(*values)
        if isinstance(node, ast.Compare):
            left = self._compile_node(node.left, symbols, z3)
            comparisons = []
            for op, rhs_node in zip(node.ops, node.comparators):
                right = self._compile_node(rhs_node, symbols, z3)
                comparisons.append(_COMPARE_OPERATORS[type(op)](left, right))
                left = right
            return z3.And(*comparisons) if len(comparisons) > 1 else comparisons[0]
        raise ValueError(f"Unsupported constraint expression: {type(node).__name__}.")

    @staticmethod
    def _split_top_level(text: str) -> list[str]:
        """Split comma-separated expressions, respecting parentheses."""
        parts: list[str] = []
        start = 0
        depth = 0
        for index, char in enumerate(text):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0:
                    raise ValueError("Unbalanced parentheses.")
            elif char == "," and depth == 0:
                parts.append(text[start:index].strip())
                start = index + 1
        if depth != 0:
            raise ValueError("Unbalanced parentheses.")
        parts.append(text[start:].strip())
        return parts


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Not: operator.not_,
}
_COMPARE_OPERATORS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}
