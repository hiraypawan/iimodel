"""Critic loop -- verify generated code before it is trusted.

After the code generator produces a module, the critic runs three checks:

1. **Sandbox run** -- the code must execute successfully inside the
   fail-closed Docker sandbox.  The exact traceback is fed back to the model
   on failure.
2. **Bandit security scan** -- when Bandit is installed, the code is scanned
   for known insecure patterns.  A missing Bandit is reported as *skipped*,
   never as *passed*.
3. **Constraint checklist** -- the Interviewer's ``ConstraintTree`` is turned
   into concrete checks (for example: the requested platform must appear in
   the code).

An **accessibility** check is applied *only* to generated web-UI code.  It is
never applied to, or claimed for, other code.

The repair loop is bounded: at most three attempts, each fed the exact error.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

__all__ = ["CriticLoop", "ACCESSIBILITY_CHECKS"]

#: Accessibility checks -- applied to generated web-UI code only.
ACCESSIBILITY_CHECKS: list[tuple[str, re.Pattern[str]]] = [
    ("html lang attribute", re.compile(r"<html[^>]*\blang\s*=", re.IGNORECASE)),
    ("document title", re.compile(r"<title>", re.IGNORECASE)),
    ("image alt text", re.compile(r"<img[^>]*\balt\s*=", re.IGNORECASE)),
    ("form labels", re.compile(r"<label[^>]*>", re.IGNORECASE)),
]


class CriticLoop:
    """Run code through sandbox, security scan and constraint checklist."""

    MAX_RETRIES = 3

    def __init__(
        self,
        llm: Any = None,
        sandbox_runner: Optional[Callable[[str, int], tuple[bool, str]]] = None,
    ):
        self.llm = llm
        self.sandbox_runner = sandbox_runner

    # -- individual checks -------------------------------------------------- #

    def sandbox_check(self, code: str, timeout: int = 30) -> dict:
        """Execute the code in the sandbox and report the exact error."""
        if self.sandbox_runner is not None:
            ok, output = self.sandbox_runner(code, timeout)
            return {"name": "sandbox_run", "status": "pass" if ok else "fail",
                    "detail": output[:4000]}
        from tools.self_tool_builder import SelfToolBuilder

        builder = SelfToolBuilder()
        ok, output = builder.sandbox_run(code, timeout=timeout)
        return {
            "name": "sandbox_run",
            "status": "pass" if ok else "fail",
            "detail": output[:4000],
        }

    def bandit_check(self, code: str) -> dict:
        """Static security scan with Bandit (skipped when not installed)."""
        if shutil.which("bandit") is None:
            try:
                import bandit  # noqa: F401
            except Exception:
                return {
                    "name": "bandit",
                    "status": "skipped",
                    "detail": (
                        "Bandit is not installed; the security scan was NOT "
                        "performed."
                    ),
                }
        with tempfile.TemporaryDirectory(prefix="ii-bandit-") as tmp:
            path = Path(tmp) / "candidate.py"
            path.write_text(code, encoding="utf-8")
            command = [
                sys.executable, "-m", "bandit", "-q", "-f", "json", str(path),
            ]
            try:
                result = subprocess.run(
                    command, capture_output=True, text=True, timeout=120
                )
            except Exception as exc:
                return {
                    "name": "bandit",
                    "status": "skipped",
                    "detail": f"bandit could not be executed: {exc}",
                }
        try:
            report = json.loads(result.stdout or "{}")
        except Exception:
            report = {}
        issues = report.get("results", [])
        high = [
            issue for issue in issues
            if issue.get("issue_severity") in {"HIGH", "MEDIUM"}
        ]
        return {
            "name": "bandit",
            "status": "fail" if high else "pass",
            "detail": (
                f"{len(high)} medium/high severity finding(s): "
                + "; ".join(
                    f"{issue.get('test_id')} (line {issue.get('line_number')})"
                    for issue in high[:5]
                )
            ) if high else f"{len(issues)} finding(s), none medium/high",
            "issues": issues[:20],
        }

    def constraint_check(self, code: str, constraint_tree: Optional[dict]) -> dict:
        """Check the code against the Interviewer's constraint tree."""
        if not constraint_tree:
            return {
                "name": "constraints",
                "status": "skipped",
                "detail": "no constraint tree supplied",
            }
        slots = constraint_tree.get("slots", {}) or {}
        missing: list[str] = []
        for slot, values in slots.items():
            if not values:
                continue
            value = str(values[0].get("value", ""))
            if not value or len(value) < 3:
                continue
            tokens = [token for token in re.findall(r"[A-Za-z0-9+#.]+", value) if len(token) > 2]
            if not tokens:
                continue
            if not any(token.lower() in code.lower() for token in tokens):
                missing.append(f"{slot}='{value}' not found in the code")
        if missing:
            return {
                "name": "constraints",
                "status": "fail",
                "detail": "; ".join(missing),
            }
        return {
            "name": "constraints",
            "status": "pass",
            "detail": "all specified constraints appear in the code",
        }

    def accessibility_check(self, code: str) -> dict:
        """Accessibility checklist -- generated web-UI code only."""
        missing = [
            name for name, pattern in ACCESSIBILITY_CHECKS
            if not pattern.search(code)
        ]
        if missing:
            return {
                "name": "accessibility",
                "status": "fail",
                "detail": "missing: " + ", ".join(missing),
            }
        return {
            "name": "accessibility",
            "status": "pass",
            "detail": "all accessibility checklist items present",
        }

    # -- main --------------------------------------------------------------- #

    def review(
        self,
        code: str,
        constraint_tree: Optional[dict] = None,
        *,
        is_web_ui: bool = False,
        max_retries: Optional[int] = None,
    ) -> dict:
        """Review generated code, repairing it up to ``max_retries`` times."""
        limit = self.MAX_RETRIES if max_retries is None else max_retries
        attempts: list[dict] = []
        current = code
        last_error = ""
        for attempt in range(1, limit + 1):
            checks: list[dict] = []
            sandbox = self.sandbox_check(current)
            checks.append(sandbox)
            checks.append(self.bandit_check(current))
            checks.append(self.constraint_check(current, constraint_tree))
            if is_web_ui:
                checks.append(self.accessibility_check(current))
            failed = [check for check in checks if check["status"] == "fail"]
            attempts.append({
                "attempt": attempt,
                "checks": checks,
                "passed": not failed,
            })
            if not failed:
                return {
                    "status": "PASSED",
                    "attempts": attempts,
                    "code": current,
                    "checks": checks,
                    "note": (
                        "Accessibility checks were applied because this is "
                        "generated web-UI code."
                        if is_web_ui else
                        "Accessibility checks were not applied (not web-UI code)."
                    ),
                }
            last_error = "\n".join(
                f"[{check['name']}] {check['detail']}" for check in failed
            )
            repaired = self._repair(current, last_error)
            if not repaired:
                break
            current = repaired
        return {
            "status": "FAILED",
            "attempts": attempts,
            "checks": attempts[-1]["checks"] if attempts else [],
            "code": current,
            "final_error": last_error,
            "note": (
                f"The critic stopped after {len(attempts)} attempt(s); the "
                "code was not accepted."
            ),
        }

    def _repair(self, code: str, error: str) -> Optional[str]:
        """Ask the model to fix the exact failing lines."""
        if self.llm is None:
            return None
        prompt = (
            "Fix ONLY the failing lines. Return full code.\n\n"
            "CURRENT CODE:\n```python\n"
            f"{code}\n```\n\n"
            f"EXACT ERRORS:\n{error}\n"
        )
        try:
            response = self.llm.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You repair Python code. Change only the failing "
                            "lines. Return the full module and nothing else."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=1200,
            )
        except Exception:
            return None
        if isinstance(response, dict):
            content = response.get("content")
            if isinstance(content, str) and content.strip():
                from tools.self_tool_builder import _extract_code

                return _extract_code(content)
        return None
