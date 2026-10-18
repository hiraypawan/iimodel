"""Configurable checks for generated or user-provided Python code.

The module provides syntax and text-based checks plus an optional runtime check.
A caller can ask a model to repair failed checks, but passing checks does not
prove that code is correct or safe. Runtime checks execute code under the current
OS user and are not a sandbox.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Callable

from reasoning.constraint_compiler import ConstraintCompiler, FormalProblem


@dataclass
class Constraint:
    rule_id: str
    description: str
    check: Callable[[str], tuple[bool, str]]  # (code) -> (passed, detail)
    weight: float = 1.0


class ConstraintSharder:
    """Run configured checks and optionally request repairs for failures."""

    def __init__(self):
        self.constraints: list[Constraint] = []
        self.constraint_compiler = ConstraintCompiler()
        self.formal_problem: FormalProblem | None = None

    def add_constraint(self, constraint: Constraint) -> None:
        self.constraints.append(constraint)

    def compile_formal_problem(self, problem_text: str) -> FormalProblem:
        """Parse a VARIABLES/CONSTRAINTS block with the restricted compiler."""
        self.formal_problem = self.constraint_compiler.parse_problem(problem_text)
        return self.formal_problem

    def compile_sequential_problem(
        self,
        problem: str,
        variables: dict[str, str],
        translator: Callable[[str], str],
    ) -> list[str]:
        """Translate each prose clause through the caller's one-clause adapter.

        The adapter's natural-language fidelity is outside this module's
        guarantees; returned formulas are syntax-checked before they are stored.
        """
        constraints = self.constraint_compiler.compile_all(
            problem, variables=variables, translator=translator
        )
        self.formal_problem = FormalProblem(dict(variables), tuple(constraints))
        return constraints

    def solve_formal_problem(self, problem_text: str | None = None) -> dict:
        """Solve only explicitly encoded formulas; returns an unavailable result if Z3 is absent."""
        if problem_text is not None:
            problem = self.compile_formal_problem(problem_text)
        elif self.formal_problem is not None:
            problem = self.formal_problem
        else:
            raise ValueError("Compile a formal problem before requesting a solve.")
        return self.constraint_compiler.solve(
            list(problem.constraints), problem.variables
        )

    def check_all(self, code: str) -> dict[str, tuple[bool, str]]:
        """Run every constraint against the code. Returns {rule_id: (passed, detail)}."""
        results: dict[str, tuple[bool, str]] = {}
        for constraint in self.constraints:
            try:
                passed, detail = constraint.check(code)
            except Exception as exc:  # pragma: no cover - checker bugs
                passed, detail = False, f"Checker error: {exc}"
            results[constraint.rule_id] = (passed, detail)
        return results

    def enforce(
        self,
        code: str,
        model_fix_fn: Callable[[str, str], str],
        max_rounds: int = 5,
    ) -> tuple[str, dict[str, tuple[bool, str]], int]:
        """Check code and ask ``model_fix_fn`` to revise failures for a bounded
        number of rounds. Returns (final_code, results, rounds_used).
        """
        current_code = code
        rounds_used = 0

        for round_idx in range(max_rounds):
            rounds_used = round_idx + 1
            results = self.check_all(current_code)
            failed = [rule_id for rule_id, (passed, _) in results.items() if not passed]
            if not failed:
                return current_code, results, rounds_used

            shard_detail = "\n".join(
                f"- {rule_id}: {results[rule_id][1]}" for rule_id in failed
            )
            fix_prompt = (
                "Your code failed the following checks. Fix these issues and return "
                "the complete corrected code.\n\n"
                f"Failed checks:\n{shard_detail}\n\n"
                f"Current code:\n```python\n{current_code}\n```"
            )
            fixed = model_fix_fn(fix_prompt, current_code)
            extracted = self._extract_code_block(fixed)
            # Callers may return either a fenced response or already-extracted code.
            if extracted:
                current_code = extracted
            elif fixed.strip():
                current_code = fixed.strip()
            else:
                break

        return current_code, self.check_all(current_code), rounds_used

    @staticmethod
    def check_syntax(code: str) -> tuple[bool, str]:
        """Check whether source parses as Python."""
        try:
            ast.parse(code)
            return True, "Valid Python syntax."
        except SyntaxError as exc:
            return False, f"Syntax error at line {exc.lineno}: {exc.msg}"

    @staticmethod
    def check_no_todos(code: str) -> tuple[bool, str]:
        """Flag TODO/FIXME markers; this is a text check, not semantic analysis."""
        if "TODO" in code.upper() or "FIXME" in code.upper():
            return False, "Code contains TODO/FIXME placeholders."
        return True, "No TODO/FIXME placeholders."

    @staticmethod
    def check_no_print_debug(code: str) -> tuple[bool, str]:
        """Flag print calls with a basic regular expression."""
        if re.search(r"\bprint\s*\(", code):
            return False, "Contains print() calls (review whether these are intentional)."
        return True, "No print() calls detected."

    @staticmethod
    def check_no_os_system(code: str) -> tuple[bool, str]:
        """Flag a few risky text patterns; this is not a security boundary."""
        dangerous = ("os.system(", "os.popen(", "subprocess.Popen(", "shell=True")
        for pattern in dangerous:
            if pattern in code:
                return False, f"Risky text pattern detected: {pattern}"
        return True, "No listed risky text patterns detected."

    @staticmethod
    def check_runnable(code: str, timeout: int = 5) -> tuple[bool, str]:
        """Run code in a child process; it still has the current user's permissions."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            filename = f.name
        try:
            proc = subprocess.run(
                [sys.executable, filename],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if proc.returncode == 0:
                return True, f"Process exited successfully. Output: {proc.stdout[-1000:]}"
            return False, proc.stderr[-2000:] or f"Process exited with code {proc.returncode}."
        except subprocess.TimeoutExpired:
            return False, "Process timed out; it may have side effects before termination."
        finally:
            try:
                os.unlink(filename)
            except OSError:
                pass

    @staticmethod
    def _extract_code_block(text: str) -> str | None:
        """Extract a fenced code block, if present."""
        if "```python" in text:
            return text.split("```python", 1)[1].split("```", 1)[0].strip()
        if "```" in text:
            return text.split("```", 1)[1].split("```", 1)[0].strip()
        return None


def default_constraints() -> ConstraintSharder:
    """Return basic static checks; runtime execution is intentionally opt-in."""
    sharder = ConstraintSharder()
    sharder.add_constraint(
        Constraint("syntax_ok", "Valid Python syntax", ConstraintSharder.check_syntax)
    )
    sharder.add_constraint(
        Constraint("no_todos", "No TODO/FIXME placeholders", ConstraintSharder.check_no_todos)
    )
    sharder.add_constraint(
        Constraint("no_debug_print", "Review print() calls", ConstraintSharder.check_no_print_debug)
    )
    sharder.add_constraint(
        Constraint("no_risky_patterns", "No listed risky text patterns", ConstraintSharder.check_no_os_system)
    )
    return sharder
