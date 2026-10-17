"""
Part 11 / v11 — FIX 2: Enumerative Program Synthesis (Kolmogorov Complexity)

True mathematical invention via exhaustive search over primitive building blocks.
Given input/output behavior, the synthesizer enumerates every combination of
primitives (starting from the simplest) until it finds the shortest program
that matches — the lowest Kolmogorov Complexity solution.

No neural guessing. Deterministic exhaustive search beats probabilistic
interpolation from training data.
"""

from __future__ import annotations

import ast
import itertools
import operator
from dataclasses import dataclass
from typing import Callable


@dataclass
class SynthesisResult:
    found: bool
    code: str
    lines: int
    detail: str


class EnumerativeSynthesizer:
    """
    Exhaustively generates program combinations from primitive functions
    and tests them against input/output examples.

    Primitives are simple Python callables (arithmetic, array ops, logic).
    The synthesizer builds expressions like: f(g(x), h(y)) and tests them.
    """

    def __init__(self):
        # Primitive functions the model can compose
        self.primitives: dict[str, Callable] = {
            "add": operator.add,
            "sub": operator.sub,
            "mul": operator.mul,
            "neg": operator.neg,
            "abs": abs,
            "max": max,
            "min": min,
            "mod": operator.mod,
            "pow": operator.pow,
        }
        # Primitive arity (how many args each takes)
        self.arity = {
            "add": 2, "sub": 2, "mul": 2, "neg": 1, "abs": 1,
            "max": 2, "min": 2, "mod": 2, "pow": 2,
        }

    def synthesize(
        self,
        examples: list[tuple[list, object]],
        max_lines: int = 3,
        max_combinations: int = 20000,
    ) -> SynthesisResult:
        """
        Find the shortest program that maps inputs -> outputs for all examples.

        examples: [([input_args], expected_output), ...]
        max_lines: max expression depth (number of nested primitives)
        max_combinations: safety cap to prevent runaway search
        """
        tested = 0

        # Depth 1: single primitive applied to inputs
        for depth in range(1, max_lines + 1):
            for combo in self._generate_expressions(depth):
                tested += 1
                if tested > max_combinations:
                    return SynthesisResult(
                        False, "", 0,
                        f"Exhausted {max_combinations} combinations without a match.",
                    )
                if self._matches_all(combo, examples):
                    code = self._expr_to_code(combo)
                    return SynthesisResult(
                        True, code, depth,
                        f"Found match at depth {depth} after {tested} tests.",
                    )

        return SynthesisResult(
            False, "", 0,
            f"No matching program found within max_lines={max_lines}.",
        )

    # ------------------------------------------------------------------ #
    # Internal: expression generation
    # ------------------------------------------------------------------ #
    def _generate_expressions(self, depth: int):
        """Yield nested expression tuples up to `depth` primitives."""
        # Flatten representation: (primitive_name, [arg1, arg2, ...])
        # args are either 'x0', 'x1', ... (input slots) or nested expressions
        input_slots = [f"x{i}" for i in range(8)]  # up to 8 input args

        yield from self._gen(depth, input_slots)

    def _gen(self, depth: int, slots: list[str]):
        if depth == 0:
            # Leaf: just an input slot
            for s in slots:
                yield ("slot", s)
            return

        # Option 1: leaf (input slot) — allows shorter programs to be found early
        for s in slots:
            yield ("slot", s)

        # Option 2: apply a primitive to sub-expressions
        for pname, arity in self.arity.items():
            if arity == 1:
                for sub in self._gen(depth - 1, slots):
                    yield (pname, [sub])
            elif arity == 2:
                for left in self._gen(depth - 1, slots):
                    for right in self._gen(depth - 1, slots):
                        yield (pname, [left, right])

    def _eval_expr(self, expr: tuple, env: dict) -> object:
        kind = expr[0]
        if kind == "slot":
            return env[expr[1]]
        pname = kind
        args = [self._eval_expr(a, env) for a in expr[1]]
        func = self.primitives[pname]
        try:
            return func(*args)
        except Exception:
            return None

    def _matches_all(self, expr: tuple, examples: list[tuple[list, object]]) -> bool:
        for inputs, expected in examples:
            env = {f"x{i}": v for i, v in enumerate(inputs)}
            result = self._eval_expr(expr, env)
            if result is None or result != expected:
                return False
        return True

    def _eval_expr(self, expr: tuple, env: dict) -> object:
        kind = expr[0]
        if kind == "slot":
            return env.get(expr[1])  # missing slot -> None (expression invalid for this input count)
        pname = kind
        args = [self._eval_expr(a, env) for a in expr[1]]
        if any(a is None for a in args):
            return None
        func = self.primitives[pname]
        try:
            return func(*args)
        except Exception:
            return None

    def _expr_to_code(self, expr: tuple) -> str:
        """Convert expression tree to readable Python code."""
        kind = expr[0]
        if kind == "slot":
            return expr[1]
        pname = kind
        args = [self._expr_to_code(a) for a in expr[1]]
        return f"{pname}({', '.join(args)})"


def synthesis_tool(examples_json: str) -> str:
    """
    High-level tool entry point for the II agent.
    examples_json: JSON string like [[[1, 2], 3], [[5, 7], 12]]
    Represents input/output examples to synthesize a program for.
    """
    import json

    try:
        examples = json.loads(examples_json)
    except json.JSONDecodeError:
        return "Invalid JSON. Provide examples as: [[[1, 2], 3], [[5, 7], 12]]"

    synth = EnumerativeSynthesizer()
    result = synth.synthesize(examples, max_lines=3)

    if result.found:
        return (
            f"SYNTHESIZED (Kolmogorov-optimal, {result.lines} lines):\n"
            f"{result.code}\n{result.detail}"
        )
    return f"SYNTHESIS FAILED. {result.detail}"


if __name__ == "__main__":
    # Self-test: synthesize a function that adds two numbers
    r = synthesis_tool('[[[1, 2], 3], [[5, 7], 12], [[0, 0], 0]]')
    print(r)
