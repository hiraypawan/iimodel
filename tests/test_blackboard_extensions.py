from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ii_blackboard import Blackboard, BlackboardBuilder, BlackboardInspector, InterfaceSpec


class BlackboardExtensionTests(unittest.TestCase):
    def test_failed_candidate_uses_git_restore_for_clean_tracked_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            source = root / "module.py"
            source.write_text("original_value = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "module.py"], cwd=root, check=True)
            subprocess.run(
                [
                    "git", "-c", "user.name=II tests", "-c",
                    "user.email=tests@example.invalid", "commit", "-q", "-m", "baseline",
                ],
                cwd=root,
                check=True,
            )

            board = Blackboard(root)
            board.write_interface(
                "module",
                InterfaceSpec("module", "module.py", "function", "value()", "Return value."),
            )
            board.lock_interface("module")
            builder = BlackboardBuilder(board)
            result = builder.build_file(
                "module",
                lambda _prompt: "```python\nreplacement_value = 2\n```",
                validation_fn=lambda _path: (False, "fixture build failure"),
            )

            self.assertIsNone(result)
            self.assertEqual(source.read_text(encoding="utf-8"), "original_value = 1\n")
            status = subprocess.run(
                ["git", "status", "--porcelain", "--", "module.py"],
                cwd=root, capture_output=True, text=True, check=True,
            )
            self.assertEqual(status.stdout, "")

    def test_inspector_returns_cycle_graph_as_approximate_json_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("import b\n", encoding="utf-8")
            (root / "b.py").write_text("import a\n", encoding="utf-8")
            graph = BlackboardInspector(root).dependency_graph()
        self.assertTrue(graph["approximate"])
        self.assertEqual(len(graph["cycles"]), 1)
        self.assertEqual(set(graph["cycles"][0]), {"a.py", "b.py"})


if __name__ == "__main__":
    unittest.main()
