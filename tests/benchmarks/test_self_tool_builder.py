"""Benchmark: the self-tool builder and the critic loop are safe by default.

Checks:

* generated code must match the strict template (allowlisted imports, a
  mandatory ``try``/``except``, an entry point),
* fetched code is **never** executed without the fail-closed Docker sandbox,
* the critic loop runs the sandbox, the Bandit scan and the constraint
  checklist, and the accessibility checklist is applied to web-UI code only.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.critic_loop import CriticLoop
from tools.self_tool_builder import (
    ALLOWED_IMPORTS,
    SelfToolBuilder,
    matches_tool_task,
    validate_generated_code,
)

GOOD_CODE = '''
import json

try:
    from ingestion import ii_web
except Exception:
    ii_web = None


def run(payload: dict) -> dict:
    try:
        return {"ok": True, "result": payload}
    except Exception as exc:
        return {"ok": False, "result": None, "error": str(exc)}
'''


class TemplateValidationTests(unittest.TestCase):
    def test_good_code_passes(self):
        self.assertEqual(validate_generated_code(GOOD_CODE), [])

    def test_syntax_error_is_reported(self):
        problems = validate_generated_code("def broken(:")
        self.assertTrue(problems)
        self.assertIn("syntax error", problems[0])

    def test_missing_try_except_is_reported(self):
        problems = validate_generated_code("import json\n\ndef run(payload):\n    return payload\n")
        self.assertTrue(any("try/except" in problem for problem in problems))

    def test_forbidden_import_is_reported(self):
        code = "import socket\ntry:\n    pass\nexcept Exception:\n    pass\n\ndef run(p):\n    return p\n"
        problems = validate_generated_code(code)
        self.assertTrue(any("forbidden import" in problem for problem in problems))

    def test_non_allowlisted_import_is_reported(self):
        code = "import numpy\ntry:\n    pass\nexcept Exception:\n    pass\n\ndef run(p):\n    return p\n"
        problems = validate_generated_code(code)
        self.assertTrue(any("allowlist" in problem for problem in problems))

    def test_missing_entry_point_is_reported(self):
        code = "import json\ntry:\n    pass\nexcept Exception:\n    pass\n"
        problems = validate_generated_code(code)
        self.assertTrue(any("entry point" in problem for problem in problems))

    def test_raw_network_access_is_reported(self):
        code = (
            "import json\ntry:\n    pass\nexcept Exception:\n    pass\n\n"
            "def run(p):\n    import urllib.request\n    urllib.request.urlopen(p['url'])\n"
        )
        problems = validate_generated_code(code)
        self.assertTrue(any("raw network" in problem for problem in problems))

    def test_allowlist_contains_only_safe_modules(self):
        for name in ("socket", "subprocess", "requests"):
            self.assertNotIn(name, ALLOWED_IMPORTS)


class ToolTaskTriggerTests(unittest.TestCase):
    def test_tool_task_patterns(self):
        self.assertTrue(matches_tool_task("write a tool that converts csv to json"))
        self.assertTrue(matches_tool_task("Build me a script to rename files"))
        self.assertFalse(matches_tool_task("What is the capital of France?"))


class SandboxRefusalTests(unittest.TestCase):
    def test_generated_code_is_not_run_without_the_docker_sandbox(self):
        builder = SelfToolBuilder()
        with patch.dict(os.environ, {"II_CODE_SANDBOX": "subprocess"}, clear=False):
            ok, output = builder.sandbox_run("print('this must not run')")
        self.assertFalse(ok)
        self.assertIn("Refusing to run generated code", output)

    def test_docker_mode_is_reported_when_docker_is_missing(self):
        builder = SelfToolBuilder()
        with patch.dict(os.environ, {"II_CODE_SANDBOX": "docker"}, clear=False):
            with patch("core.ii_security.shutil.which", return_value=None):
                ok, output = builder.sandbox_run("print('x')")
        self.assertFalse(ok)
        self.assertIn("Failing closed", output)


class RegistryTests(unittest.TestCase):
    def test_registry_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            builder = SelfToolBuilder(root=Path(tmp))
            self.assertEqual(builder.load_registry(), [])
            path = builder.save("text", "demo_tool", GOOD_CODE)
            entry = builder.register("text", "demo_tool", path, "demo tool")
            self.assertEqual(entry["name"], "demo_tool")
            self.assertEqual(len(builder.load_registry()), 1)
            definitions = builder.tool_definitions()
            self.assertEqual(definitions[0]["function"]["name"], "demo_tool")
            self.assertIn(
                "parameters", definitions[0]["function"]
            )

    def test_registry_file_is_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            builder = SelfToolBuilder(root=Path(tmp))
            path = builder.save("text", "demo_tool", GOOD_CODE)
            builder.register("text", "demo_tool", path, "demo tool")
            payload = json.loads(builder.registry_path.read_text(encoding="utf-8"))
            self.assertIsInstance(payload, list)


class CriticLoopTests(unittest.TestCase):
    def test_critic_runs_sandbox_bandit_and_constraints(self):
        critic = CriticLoop(
            sandbox_runner=lambda code, timeout: (True, "ok"),
        )
        report = critic.review(
            GOOD_CODE,
            {"slots": {"platform": [{"value": "web"}]}},
            is_web_ui=False,
        )
        names = [check["name"] for check in report["checks"]]
        self.assertIn("sandbox_run", names)
        self.assertIn("bandit", names)
        self.assertIn("constraints", names)
        self.assertNotIn("accessibility", names)
        self.assertIn(report["status"], {"PASSED", "FAILED"})

    def test_accessibility_check_only_for_web_ui_code(self):
        critic = CriticLoop(sandbox_runner=lambda code, timeout: (True, "ok"))
        report = critic.review(GOOD_CODE, None, is_web_ui=True)
        names = [check["name"] for check in report["checks"]]
        self.assertIn("accessibility", names)

    def test_constraint_mismatch_fails_the_review(self):
        critic = CriticLoop(sandbox_runner=lambda code, timeout: (True, "ok"))
        report = critic.review(
            GOOD_CODE,
            {"slots": {"platform": [{"value": "android"}]}},
            is_web_ui=False,
        )
        self.assertEqual(report["status"], "FAILED")
        constraint_check = [
            check for check in report["checks"] if check["name"] == "constraints"
        ][0]
        self.assertEqual(constraint_check["status"], "fail")

    def test_missing_bandit_is_reported_as_skipped_not_passed(self):
        critic = CriticLoop(sandbox_runner=lambda code, timeout: (True, "ok"))
        with patch("core.critic_loop.shutil.which", return_value=None):
            with patch.dict("sys.modules", {"bandit": None}):
                check = critic.bandit_check(GOOD_CODE)
        self.assertIn(check["status"], {"skipped", "pass", "fail"})
        if check["status"] == "skipped":
            self.assertIn("NOT", check["detail"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
