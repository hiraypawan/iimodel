from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import networkx as nx

from ingestion.license_checker import check_license
from ingestion.repo_ingestor import RepoIngestor


class RepoIngestionTests(unittest.TestCase):
    def test_license_filter_accepts_allowed_and_rejects_gpl_or_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "LICENSE").write_text(
                "MIT License\nPermission is hereby granted, free of charge.\nThe Software is provided as is.",
                encoding="utf-8",
            )
            self.assertEqual(check_license(root)[0:2], (True, "MIT"))
            (root / "LICENSE").write_text(
                "SPDX-License-Identifier: Apache-2.0\n", encoding="utf-8"
            )
            self.assertEqual(check_license(root)[0:2], (True, "Apache-2.0"))
            (root / "LICENSE").write_text(
                "SPDX-License-Identifier: MIT OR Apache-2.0\n", encoding="utf-8"
            )
            self.assertEqual(check_license(root)[0:2], (False, "AMBIGUOUS"))
            (root / "LICENSE").write_text(
                "MIT License\nPermission is hereby granted, free of charge.\n"
                "The Software is provided as is.\n\nApache License Version 2.0",
                encoding="utf-8",
            )
            self.assertEqual(check_license(root)[0:2], (False, "AMBIGUOUS"))
            (root / "LICENSE").write_text(
                "SPDX-License-Identifier: GPL-3.0-only\n", encoding="utf-8"
            )
            self.assertEqual(check_license(root)[0:2], (False, "GPL"))
            (root / "LICENSE").write_text(
                "GNU GENERAL PUBLIC LICENSE Version 3", encoding="utf-8"
            )
            self.assertEqual(check_license(root)[0:2], (False, "GPL"))
            (root / "LICENSE").unlink()
            self.assertFalse(check_license(root)[0])

    def test_github_url_validation_rejects_non_github_and_path_traversal(self):
        self.assertEqual(
            RepoIngestor.parse_github_url("https://github.com/example/project.git")[:2],
            ("example", "project"),
        )
        for url in ("http://github.com/example/project", "https://example.com/a/b", "https://github.com/a/b/tree/main"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                RepoIngestor.parse_github_url(url)

    def test_graph_cache_roundtrip_does_not_use_pickle(self):
        graph = nx.DiGraph()
        graph.add_node("a.py", type="file")
        graph.add_node("b.py", type="file")
        graph.add_edge("a.py", "b.py", relation="imports_resolved")
        with tempfile.TemporaryDirectory() as directory:
            path = RepoIngestor.save_graph_cache(
                Path(directory) / ".graph_cache", graph,
                source_url="https://github.com/example/project.git",
                license_type="MIT",
            )
            restored, metadata = RepoIngestor.load_graph_cache(path)
        self.assertTrue(restored.has_edge("a.py", "b.py"))
        self.assertEqual(metadata["license_type"], "MIT")


if __name__ == "__main__":
    unittest.main()
