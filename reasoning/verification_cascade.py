"""Static preflight and optional, explicitly injected verification gates.

The built-in Gate 1 checks syntax, requested symbols, and heuristic risk calls.
Gates 2 and 3 require caller-provided test/sandbox and entailment adapters; the
package does not execute candidate code or call a language model by default.
No independence, false-positive rate, safety, or correctness guarantee is implied.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class GateResult:
    name: str
    status: str  # pass, fail, warn, skipped
    detail: str


@dataclass(frozen=True)
class VerificationReport:
    passed: bool
    checks: tuple[GateResult, ...]
    warnings: tuple[str, ...]
    execution_performed: bool = False
    sandboxed: bool = False

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": [asdict(check) for check in self.checks],
            "warnings": list(self.warnings),
            "execution_performed": self.execution_performed,
            "sandboxed": self.sandboxed,
        }


class VerificationCascade:
    """Run a static gate plus optional differential-test and entailment hooks."""

    DEFAULT_RISKY_CALLS = {"eval", "exec", "compile", "__import__"}
    DEFAULT_RISKY_ATTRIBUTES = {
        ("os", "system"), ("os", "popen"), ("subprocess", "Popen")
    }

    def __init__(self):
        self._last_gate_status: dict[str, str] = {}
        self._last_gate_details: dict[str, str] = {}

    def gate1_deterministic(
        self,
        code: str,
        required_symbols: Iterable[str] = (),
        forbidden_calls: Iterable[str] = (),
    ) -> tuple[bool, str]:
        """Run syntax/name/risk checks; no code execution or static typing claim."""
        report = self.verify_python(code, required_symbols, forbidden_calls)
        failures = [item.detail for item in report.checks if item.status == "fail"]
        status = "pass" if report.passed else "fail"
        detail = "; ".join(failures) if failures else "Static AST checks completed."
        if report.warnings:
            detail += " " + " ".join(report.warnings)
        self._last_gate_status["gate1_deterministic"] = status
        self._last_gate_details["gate1_deterministic"] = detail
        return report.passed, detail

    def gate2_differential_fuzzing(
        self,
        code: str,
        docstring: str,
        *,
        case_generator: Callable[[str], list[Any]] | None = None,
        sandbox_runner: Callable[[str, list[Any]], tuple[bool, str]] | None = None,
    ) -> tuple[bool, str]:
        """Run caller-generated examples through a caller-supplied isolated runner.

        ``case_generator`` receives only the docstring. This module does not
        generate assertions itself and will not execute source in a subprocess.
        The caller is responsible for ensuring ``sandbox_runner`` is genuinely
        isolated and resource-limited; a function name cannot establish that.
        """
        if case_generator is None or sandbox_runner is None:
            detail = "Skipped: case generator and isolated test runner are not configured."
            self._last_gate_status["gate2_differential_fuzzing"] = "skipped"
            self._last_gate_details["gate2_differential_fuzzing"] = detail
            return False, detail
        try:
            cases = case_generator(docstring)
            if not isinstance(cases, list) or len(cases) != 3:
                raise ValueError("case generator must return exactly three test cases.")
            passed, detail = sandbox_runner(code, cases)
            status = "pass" if passed else "fail"
            self._last_gate_status["gate2_differential_fuzzing"] = status
            self._last_gate_details["gate2_differential_fuzzing"] = str(detail)
            return bool(passed), str(detail)
        except Exception as exc:
            detail = f"Gate 2 adapter failed: {exc}"
            self._last_gate_status["gate2_differential_fuzzing"] = "fail"
            self._last_gate_details["gate2_differential_fuzzing"] = detail
            return False, detail

    def gate3_binary_entailment(
        self,
        step: str,
        requirements: list[str],
        *,
        entailment_fn: Callable[[str, str], Any] | None = None,
    ) -> tuple[bool, str]:
        """Ask an injected judge a separate yes/no question per requirement.

        The judge's answers are fallible and uncalibrated. No model is invoked by
        default. Only explicit ``yes``/``true`` answers count as passing.
        """
        if not requirements or entailment_fn is None:
            detail = "Skipped: requirements or an entailment judge are not configured."
            self._last_gate_status["gate3_binary_entailment"] = "skipped"
            self._last_gate_details["gate3_binary_entailment"] = detail
            return False, detail
        answers: list[str] = []
        try:
            for requirement in requirements:
                answer = entailment_fn(step, requirement)
                normalized = str(answer).strip().lower()
                if normalized not in {"yes", "no", "true", "false"}:
                    raise ValueError("entailment judge must answer yes/no or true/false.")
                answers.append(normalized)
            passed = all(answer in {"yes", "true"} for answer in answers)
            detail = "All atomic checks returned yes." if passed else "At least one atomic check returned no."
            self._last_gate_status["gate3_binary_entailment"] = "pass" if passed else "fail"
            self._last_gate_details["gate3_binary_entailment"] = detail
            return passed, detail
        except Exception as exc:
            detail = f"Gate 3 adapter failed: {exc}"
            self._last_gate_status["gate3_binary_entailment"] = "fail"
            self._last_gate_details["gate3_binary_entailment"] = detail
            return False, detail

    def verify(self, code: str, context: dict[str, Any] | None = None) -> tuple[bool, str, dict]:
        """Run all configured gates; skipped gates prevent an all-gates pass."""
        context = context or {}
        self._last_gate_status.clear()
        self._last_gate_details.clear()
        self.gate1_deterministic(
            code,
            required_symbols=context.get("required_symbols", ()),
            forbidden_calls=context.get("forbidden_calls", ()),
        )
        self.gate2_differential_fuzzing(
            code,
            context.get("docstring", ""),
            case_generator=context.get("case_generator"),
            sandbox_runner=context.get("sandbox_runner"),
        )
        self.gate3_binary_entailment(
            context.get("step", code),
            context.get("requirements", []),
            entailment_fn=context.get("entailment_fn"),
        )
        gates = [
            {
                "name": name,
                "status": self._last_gate_status.get(name, "skipped"),
                "detail": self._last_gate_details.get(name, "Not run."),
            }
            for name in (
                "gate1_deterministic",
                "gate2_differential_fuzzing",
                "gate3_binary_entailment",
            )
        ]
        passed = all(gate["status"] == "pass" for gate in gates)
        return passed, "All configured gates passed." if passed else "Cascade incomplete or failed.", {
            "passed": passed,
            "gates": gates,
            "test_runner_called": self._last_gate_status.get("gate2_differential_fuzzing") in {"pass", "fail"},
            "execution_performed": None,
            "sandboxed": False,
            "isolation_verified": False,
        }

    def best_of_k(
        self,
        prompt: str,
        k: int = 4,
        *,
        generator_fn: Callable[[str], str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Return the first generated candidate passing all configured gates.

        No generation backend is called unless the caller supplies one. An empty
        string means no candidate passed; inspect ``last_best_of_k`` for details.
        """
        if k < 1:
            raise ValueError("k must be at least 1.")
        if generator_fn is None:
            raise NotImplementedError("best_of_k requires a caller-supplied generator.")
        attempts = []
        for index in range(k):
            candidate = generator_fn(prompt)
            passed, detail, report = self.verify(candidate, context=context)
            attempts.append({"index": index, "passed": passed, "report": report})
            if passed:
                self.last_best_of_k = {"attempts": attempts, "selected": index}
                return candidate
        self.last_best_of_k = {"attempts": attempts, "selected": None}
        return ""

    def verify_python(
        self,
        code: str,
        required_symbols: Iterable[str] = (),
        forbidden_calls: Iterable[str] = (),
    ) -> VerificationReport:
        """Run syntax, caller-specified symbol, and heuristic risk checks."""
        checks: list[GateResult] = []
        warnings: list[str] = []
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            checks.append(GateResult("syntax", "fail", f"Line {exc.lineno}: {exc.msg}"))
            checks.extend(
                [
                    GateResult("required_symbols", "skipped", "Source did not parse."),
                    GateResult("risk_scan", "skipped", "Source did not parse."),
                ]
            )
            return VerificationReport(False, tuple(checks), tuple(warnings))

        checks.append(GateResult("syntax", "pass", "Python AST parsed successfully."))
        defined = {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        required = set(required_symbols)
        missing = sorted(required - defined)
        if missing:
            checks.append(GateResult("required_symbols", "fail", "Missing: " + ", ".join(missing)))
        elif required:
            checks.append(GateResult("required_symbols", "pass", "All required names are defined."))
        else:
            checks.append(GateResult("required_symbols", "skipped", "No symbol requirements supplied."))

        risky = self._find_risky_calls(tree)
        configured_forbidden = set(forbidden_calls)
        matched_forbidden = sorted(call for call in risky if call in configured_forbidden)
        unconfigured_risky = sorted(call for call in risky if call not in configured_forbidden)
        if matched_forbidden:
            checks.append(
                GateResult(
                    "risk_scan", "fail",
                    "Configured forbidden calls found: " + ", ".join(matched_forbidden),
                )
            )
        elif unconfigured_risky:
            checks.append(
                GateResult(
                    "risk_scan", "warn",
                    "Review potentially risky calls: " + ", ".join(unconfigured_risky),
                )
            )
            warnings.append("The AST risk scan is heuristic and is not a security boundary.")
        else:
            checks.append(GateResult("risk_scan", "pass", "No calls from the built-in heuristic list were detected."))

        passed = not any(check.status == "fail" for check in checks)
        return VerificationReport(False if not passed else True, tuple(checks), tuple(warnings))

    def _find_risky_calls(self, tree: ast.AST) -> set[str]:
        found: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in self.DEFAULT_RISKY_CALLS:
                found.add(node.func.id)
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                pair = (node.func.value.id, node.func.attr)
                if pair in self.DEFAULT_RISKY_ATTRIBUTES:
                    found.add(".".join(pair))
        return found
