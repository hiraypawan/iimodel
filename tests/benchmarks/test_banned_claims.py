"""Benchmark: the repository contains no banned claims.

The project has a fixed honesty contract (the "status taxonomy" and the
"banned claims" list).  This test greps the whole repository for the banned
claim strings and fails if any of them appears.

The scanner excludes itself (it has to contain the patterns to look for),
the virtual environment, and byte-code caches.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: The banned claims, as they would appear in a claim.
BANNED_CLAIMS: list[tuple[str, str]] = [
    ("invention verified", r"invention\s+verified"),
    ("0% hallucination", r"(?:~|≈|about\s+)?0\s*%\s*hallucination"),
    ("infinite context", r"infinite\s+context"),
    ("infinite exocortex", r"infinite\s+exocortex"),
    ("rupees 0 forever", r"₹\s*0\s*forever"),
    ("percent efficiency proven", r"\d+\s*%\s*efficiency\s+proven"),
    ("symbolic ai", r"symbolic\s+ai"),
    ("prolog", r"\bprolog\b"),
    ("universal os", r"universal\s+os"),
]

#: Files that are not scanned.
EXCLUDED_DIRS = {".git", ".venv", "venv", "__pycache__", ".mypy_cache", "node_modules"}
EXCLUDED_FILES = {Path(__file__).name}

SCANNED_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".html", ".htm", ".js", ".css", ".sh",
    ".bat", ".yml", ".yaml", ".cfg", ".ini", ".toml",
}


def _scannable_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.name in EXCLUDED_FILES:
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in SCANNED_SUFFIXES:
            continue
        files.append(path)
    return files


class BannedClaimsTests(unittest.TestCase):
    def test_repository_contains_no_banned_claims(self):
        offenders: list[str] = []
        for path in _scannable_files():
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for label, pattern in BANNED_CLAIMS:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    line_number = text[: match.start()].count("\n") + 1
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{line_number}: {label}"
                    )
        self.assertEqual(
            offenders, [],
            "banned claims found in the repository:\n" + "\n".join(offenders),
        )

    def test_banned_claim_list_is_not_empty(self):
        self.assertGreaterEqual(len(BANNED_CLAIMS), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
