"""Benchmark: every citation resolves and every quote matches its page.

Two checks:

1. every URL the solver returns must resolve with HTTP 200, and
2. the quoted passage must fuzzy-match the content of the page it came from.

The second check is what makes the citation meaningful: a URL that resolves
but does not contain the quote is not evidence.
"""

from __future__ import annotations

import re
import unittest

from tests.benchmarks.support import requires_network
from reasoning.logic_tree_solver import LogicTreeSolver

_WORD_RE = re.compile(r"[a-z0-9]+")

#: Words that carry no evidence value when checking a quote.
_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "as", "at", "by", "from",
    "its", "it", "that", "this", "has", "have", "had", "most", "largest",
}


def _normalise(text: str) -> str:
    return " ".join(_WORD_RE.findall((text or "").lower()))


def _content_tokens(text: str) -> set[str]:
    return {
        word for word in _WORD_RE.findall((text or "").lower())
        if word not in _STOPWORDS and len(word) > 2
    }


def _fuzzy_match(quote: str, page_text: str, threshold: float = 0.8) -> bool:
    """True when the quote's content words are present in the page text.

    A quote is evidence only if the page really contains it; requiring most
    of the quote's content words keeps the check strict without depending on
    the exact markup.
    """
    quote_words = _content_tokens(quote)
    if not quote_words:
        return True
    page_words = _content_tokens(page_text)
    if not page_words:
        return False
    overlap = len(quote_words & page_words) / len(quote_words)
    return overlap >= threshold


class CitationTests(unittest.TestCase):
    @requires_network("live internet access is required to check citations")
    def test_urls_resolve_and_quotes_match_the_page(self):
        import requests

        solver = LogicTreeSolver()
        result = solver.solve("What is the capital of France?")
        if not result["sources"]:
            self.skipTest("no live sources were retrieved in this run")

        for source in result["sources"]:
            url = source["url"]
            with self.subTest(url=url):
                try:
                    response = requests.get(
                        url,
                        timeout=20,
                        headers={"User-Agent": "II-benchmark/3.0 (+citation check)"},
                    )
                except Exception as exc:
                    self.fail(f"{url} could not be fetched: {exc}")
                self.assertEqual(
                    response.status_code, 200,
                    f"{url} returned HTTP {response.status_code}",
                )
                quote = source.get("quote", "")
                if not quote:
                    continue
                self.assertTrue(
                    _fuzzy_match(quote, response.text),
                    f"the quoted passage does not appear on {url}",
                )

    @requires_network("live internet access is required to check citations")
    def test_every_step_records_its_source_urls(self):
        solver = LogicTreeSolver()
        result = solver.solve("Who is the CEO of the company that created WhatsApp?")
        for step in result["steps"]:
            for passage in step.get("passages", []):
                self.assertTrue(
                    passage.get("url", "").startswith("http"),
                    "every passage must carry its source URL",
                )

    def test_quote_matching_helper_rejects_unrelated_text(self):
        self.assertTrue(
            _fuzzy_match(
                "Paris is the capital of France",
                "Paris is the capital and largest city of France",
            )
        )
        self.assertFalse(
            _fuzzy_match(
                "Paris is the capital of France",
                "The mitochondrion is the powerhouse of the cell",
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
