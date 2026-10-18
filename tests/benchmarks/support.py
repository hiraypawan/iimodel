"""Shared helpers for the II benchmark suite.

The benchmarks use **external ground truth only**.  Nothing in this package
asserts that II agrees with itself: the expected answers live in
``qa_ground_truth.json`` and are checked against live sources.

Network-dependent tests are skipped (not failed) when the sandbox has no
internet access, so the suite stays green offline while still being strict
when a network is available.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parent
GROUND_TRUTH_PATH = BENCHMARK_DIR / "qa_ground_truth.json"

__all__ = [
    "GROUND_TRUTH_PATH",
    "load_ground_truth",
    "network_available",
    "requires_network",
    "FakeLLM",
    "offline_fetcher",
]


def load_ground_truth() -> list[dict]:
    """Load the 20-question external ground-truth set."""
    with GROUND_TRUTH_PATH.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("qa_ground_truth.json contains no questions")
    return questions


_network_state: bool | None = None


def network_available() -> bool:
    """Return True when the *search backends* are reachable.

    A plain TCP check is not enough: some sandboxes allow a socket to open
    while blocking the actual HTTPS request the backends perform.  The check
    therefore performs a real (tiny) HTTPS request against the same hosts the
    ingestion layer uses.
    """
    global _network_state
    if _network_state is None:
        if os.environ.get("II_BENCHMARK_OFFLINE") == "1":
            _network_state = False
        else:
            _network_state = _probe_backends()
    return _network_state


def _probe_backends() -> bool:
    try:
        import requests
    except Exception:
        return False
    probes = (
        "https://html.duckduckgo.com/html/?q=ii",
        "https://en.wikipedia.org/wiki/Main_Page",
    )
    for url in probes:
        try:
            response = requests.get(
                url,
                timeout=8,
                headers={"User-Agent": "II-benchmark/3.0 (+connectivity probe)"},
            )
            if response.status_code < 500:
                return True
        except Exception:
            continue
    return False


def requires_network(reason: str = "live internet access is required"):
    """Skip a test when the sandbox has no outbound connectivity."""
    return unittest.skipUnless(
        network_available(),
        reason or "live internet access is required for this benchmark",
    )


class FakeLLM:
    """A scripted stand-in for the language model.

    The benchmarks never measure model quality; they use this to make the
    renderer and the repair loop deterministic and offline.
    """

    mode = "fake"

    def __init__(self, responses: list[str] | None = None, default: str = ""):
        self.responses = list(responses or [])
        self.default = default
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None, temperature=0.0, max_tokens=1024) -> dict:
        self.calls.append(list(messages))
        if self.responses:
            return {"content": self.responses.pop(0), "tool_calls": []}
        return {"content": self.default, "tool_calls": []}

    def complete(self, prompt: str, **kwargs) -> str:
        self.calls.append([{"role": "user", "content": prompt}])
        if self.responses:
            return self.responses.pop(0)
        return self.default


def offline_fetcher(pages: dict[str, list[dict]] | None = None):
    """Return a fetcher that serves fixed pages (no network access)."""

    def fetch(query: str, max_results: int) -> list[dict]:
        if not pages:
            return []
        for needle, entries in pages.items():
            if needle.lower() in (query or "").lower():
                return entries[:max_results]
        return []

    return fetch


def page(url: str, title: str, sentences: list[str]) -> dict:
    """Build a fake fetched page."""
    return {
        "url": url,
        "title": title,
        "text": " ".join(sentences),
    }
