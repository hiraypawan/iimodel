"""
Module: ii_constraints.py — Constraint Sharding Engine

The 1.5B model cannot hold 15+ simultaneous constraints in its narrow hidden
state. Constraint Sharding solves this by:

  Pass N: Give the model only rules subset [N]
  Python wrapper: Checks ALL 15 rules
  Pass N+1: Feed back ONLY failed rules
  Repeat until all 15 pass

This achieves "perfect constraint tracking" without a giant hidden state.
"""

from __future__ import annotations

import ast
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Constraint:
    rule_id: str
    description: str
    check: Callable[[str], tuple[bool, str]]  # (code) -> (passed, detail)
    weight: float = 1.0


class ConstraintSharder:
    """
    Manages a set of constraints and iteratively enforces them via sharding.

    Usage:
        sharder = ConstraintSharder()
        sharder.add_constraint(Constraint("imports_valid", "...", check_imports))
        sharder.add_constraint(Constraint("syntax_ok", "...", check_syntax))
        result = sharder.enforce(code, model_fix_fn=fix_code_with_model)
    """

    def __init__(self):
        self.constraints: list[Constraint] = []

    def add_constraint(self, constraint: Constraint) -> None:
        self.constraints.append(constraint)

    def check_all(self, code: str) -> dict[str, tuple[bool, str]]:
        """Run every constraint against the code. Returns {rule_id: (passed, detail)}."""
        results: dict[str, tuple[bool, str]] = {}
        for c in self.constraints:
            try:
                passed, detail = c.check(code)
            except Exception as exc:  # pragma: no cover - checker bugs
                passed, detail = False, f"Checker error: {exc}"
            results[c.rule_id] = (passed, detail)
        return results

    def enforce(
        self,
        code: str,
        model_fix_fn: Callable[[str, str], str],
        max_rounds: int = 5,
    ) -> tuple[str, dict[str, tuple[bool, str]], int]:
        """
        Iteratively shard constraints: give model only failing rules,
        re-run, repeat until all pass or max_rounds reached.

        Returns: (final_code, results, rounds_used)
        """
        current_code = code
        rounds_used = 0

        for round_idx in range(max_rounds):
            rounds_used = round_idx + 1
            results = self.check_all(current_code)
            failed = [rid for rid, (p, _) in results.items() if not p]

            if not failed:
                return current_code, results, rounds_used

            # Build shard: only failed rules + their details
            shard_detail = "\n".join(
                f"- {rid}: {results[rid][1]}" for rid in failed
            )
            fix_prompt = (
                "Your code failed the following constraints. Fix ONLY these issues.\n"
                "Return the complete corrected code in a single ```python block.\n\n"
                f"Failed constraints:\n{shard_detail}\n\n"
                f"Current code:\n```python\n{current_code}\n```"
            )
            fixed = model_fix_fn(fix_prompt, current_code)
            current_code = self._extract_code_block(fixed) or current_code

        # Final check after all rounds
        results = self.check_all(current_code)
        return current_code, results, rounds_used

    # ------------------------------------------------------------------ #
    # Built-in constraint checkers
    # ------------------------------------------------------------------ #
    @staticmethod
    def check_syntax(code: str) -> tuple[bool, str]:
        try:
            ast.parse(code)
            return True, "Valid Python syntax."
        except SyntaxError as exc:
            return False, f"Syntax error at line {exc.lineno}: {exc.msg}"

    @staticmethod
    def check_no_todos(code: str) -> tuple[bool, str]:
        if "TODO" in code.upper() or "FIXME" in code.upper():
            return False, "Code contains TODO/FIXME placeholders."
        return True, "No TODO/FIXME placeholders."

    @staticmethod
    def check_no_print_debug(code: str) -> tuple[bool, str]:
        if re := __import__("re"):
            if re.search(r"\bprint\s*\(", code):
                return False, "Contains debug print() statements."
        return True, "No debug print() statements."

    @staticmethod
    def check_runnable(code: str) -> tuple[bool, str]:
        """Executes code in a subprocess sandbox. Returns (ok, output_or_error)."""
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(code)
            temp_path = f.name
        try:
            result = subprocess.run(
                ["python", temp_path],
                capture_output=True,
                text=True,
                timeout=15,
            )
            os.unlink(temp_path)
            if result.returncode == 0:
                return True, f"Ran successfully. Output: {result.stdout[:200]}"
            return False, f"Runtime error: {result.stderr[:400]}"
        except subprocess.TimeoutExpired:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            return False, "Execution timed out (possible infinite loop)."
        except Exception as exc:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            return False, f"Runner error: {exc}"

    @staticmethod
    def check_no_os_system(code: str) -> tuple[bool, str]:
        """Security: forbid os.system / subprocess shell=True (basic bandit-like)."""
        dangerous = ["os.system(", "os.popen(", "subprocess.Popen(", "shell=True"]
        for d in dangerous:
            if d in code:
                return False, f"Dangerous call detected: {d}"
        return True, "No dangerous OS calls."

    @staticmethod
    def _extract_code_block(text: str) -> str | None:
        if "```python" in text:
            block = text.split("```python", 1)[1]
            return block.split("```", 1)[0].strip()
        return None


# Default constraint preset for general coding tasks
def default_constraints() -> ConstraintSharder:
    s = ConstraintSharder()
    s.add_constraint(Constraint("syntax_ok", "Valid Python syntax", ConstraintSharder.check_syntax))
    s.add_constraint(Constraint("no_todos", "No TODO/FIXME", ConstraintSharder.check_no_todos))
    s.add_constraint(Constraint("no_debug_print", "No debug print()", ConstraintSharder.check_no_print_debug))
    s.add_constraint(Constraint("runnable", "Code runs without error", ConstraintSharder.check_runnable))
    s.add_constraint(Constraint("no_os_system", "No dangerous OS calls", ConstraintSharder.check_no_os_system))
    return s
