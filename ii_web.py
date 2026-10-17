"""
Module: ii_web.py — Web Search + Clean Extraction (Trafilatura)

Provides the II agent with live internet access:
  - search_web(query) -> DuckDuckGo HTML results (title, link, snippet)
  - read_url(url) -> clean Markdown article text via Trafilatura
  - fetch_with_authority(url) -> extraction + HTTP Last-Modified / domain authority tag

This is the "internet-as-brain" layer. The model does not memorize the web;
it queries it live and reads clean, deterministic text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import trafilatura
from duckduckgo_search import DDGS


@dataclass
class SearchResult:
    title: str
    link: str
    snippet: str


@dataclass
class FetchResult:
    url: str
    title: Optional[str]
    text: str
    last_modified: Optional[str]
    domain_authority: int
    fetched_at: str
    success: bool
    error: Optional[str] = None


def _domain_authority(domain: str) -> int:
    """
    Heuristic domain-authority score (0-100).
    Deterministic, no external API required. Used to filter web junk.
    """
    trusted_tlds = (".edu", ".gov", ".org")
    trusted_domains = (
        "github.com", "stackoverflow.com", "mozilla.org",
        "python.org", "docs.python.org", "wikipedia.org",
        "nginx.com", "cloudflare.com", "microsoft.com",
    )
    score = 50
    if any(domain.endswith(t) for t in trusted_tlds):
        score += 20
    if any(domain.endswith(d) or domain == d for d in trusted_domains):
        score += 20
    # Penalty for obvious content-farm patterns
    if re.search(r"(cheap|best|top|review|202[0-9])\.com$", domain):
        score -= 10
    return max(0, min(100, score))


def search_web(query: str, max_results: int = 5, region: str = "wt-wt") -> str:
    """
    Search DuckDuckGo and return JSON-serialized results.
    The model consumes this as structured text, not raw HTML.
    """
    results: list[SearchResult] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, region=region, max_results=max_results):
                results.append(SearchResult(
                    title=r.get("title", ""),
                    link=r.get("href", ""),
                    snippet=r.get("body", ""),
                ))
    except Exception as exc:  # pragma: no cover - network dependent
        return json.dumps({"error": f"Search failed: {exc}"})

    return json.dumps(
        [{"title": x.title, "link": x.link, "snippet": x.snippet} for x in results],
        ensure_ascii=False,
        indent=2,
    )


def read_url(url: str, max_chars: int = 4000) -> str:
    """
    Fetch a URL and extract clean Markdown article text via Trafilatura.
    Truncates to max_chars to protect the small model's context window.
    """
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return "Extraction failed: could not fetch URL."
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            no_fallback=True,
        )
        if not text:
            return "Extraction failed: no readable content found."
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[truncated]"
        return text
    except Exception as exc:  # pragma: no cover - network dependent
        return f"Extraction failed: {exc}"


def fetch_with_authority(url: str, max_chars: int = 4000) -> FetchResult:
    """
    Fetch a URL, extract clean text, and attach authority metadata:
      - HTTP Last-Modified header (recency)
      - Domain authority score
    The model can weight claims by (authority × recency).
    """
    import urllib.request
    from urllib.parse import urlparse

    fetched_at = datetime.now(timezone.utc).isoformat()
    domain = urlparse(url).netloc.lower()
    authority = _domain_authority(domain)
    last_modified: Optional[str] = None

    try:
        # Try to read Last-Modified header without downloading the body twice
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as resp:
            lm = resp.headers.get("Last-Modified")
            if lm:
                last_modified = lm
    except Exception:
        pass  # header fetch is best-effort

    text = read_url(url, max_chars=max_chars)
    success = not text.startswith("Extraction failed") and not text.startswith(
        "Extraction failed"
    )

    return FetchResult(
        url=url,
        title=None,
        text=text,
        last_modified=last_modified,
        domain_authority=authority,
        fetched_at=fetched_at,
        success=success,
        error=None if success else text,
    )


def format_source_tag(result: FetchResult) -> str:
    """
    Produce an XML-style source tag for injection into the model prompt:
      <source url="..." date="..." authority="N">...</source>
    Helps the model weigh contradictory sources deterministically.
    """
    date = result.last_modified or "unknown"
    return (
        f'<source url="{result.url}" date="{date}" '
        f'authority="{result.domain_authority}">\n'
        f'{result.text}\n</source>'
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ii_web.py \"search query\"")
        sys.exit(1)
    query = sys.argv[1]
    print(search_web(query, max_results=3))