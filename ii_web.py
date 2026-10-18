"""Optional web search and URL text extraction helpers.

These functions make outbound network requests. Search results, extracted page
text, modification dates, and heuristic domain scores may be incomplete or
incorrect; they are context for the model, not source verification.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import escape
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
    heuristic_domain_score: int
    fetched_at: str
    success: bool
    error: Optional[str] = None
    evidence_passages: list[dict[str, str]] = field(default_factory=list)


def _heuristic_domain_score(domain: str) -> int:
    """Return a simple rule-based score; it is not a reputation assessment."""
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
    """Fetch a URL and extract readable text, truncated to ``max_chars``."""
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


def extract_evidence_passages(
    text: str,
    source_url: str,
    *,
    max_passages: int = 5,
    max_chars: int = 600,
) -> list[dict[str, str]]:
    """Retain short exact substrings with their source URL for later review.

    These snippets are quotations from extracted page text, not verified
    evidence that a claim is true or that extraction preserved page context.
    """
    if max_passages < 0 or max_chars < 1:
        raise ValueError("max_passages must be non-negative and max_chars positive.")
    if max_passages == 0:
        return []
    passages: list[dict[str, str]] = []
    for candidate in re.split(r"(?<=[.!?])\s+|\n+", text):
        quote = candidate.strip()
        if not quote:
            continue
        if len(quote) > max_chars:
            quote = quote[:max_chars].rstrip() + "…"
        passages.append({"source_url": source_url, "quote": quote})
        if len(passages) >= max_passages:
            break
    return passages


def candidate_contradictions(passages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Flag rough lexical/numeric disagreements for human review only."""
    stopwords = {"the", "and", "for", "with", "from", "that", "this", "was", "are", "is", "to", "of", "in", "on", "a", "an"}
    negation = re.compile(r"\b(?:not|no|never|neither|without|isn't|wasn't|doesn't|didn't)\b", re.I)
    numbers = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?%?")
    parsed = []
    for passage in passages:
        quote = passage.get("quote", "")
        words = {word.lower() for word in re.findall(r"[A-Za-z]{3,}", quote)} - stopwords
        parsed.append((passage, words, set(numbers.findall(quote)), bool(negation.search(quote))))
    conflicts: list[dict[str, str]] = []
    for index, left in enumerate(parsed):
        for right in parsed[index + 1:]:
            if left[0].get("source_url") == right[0].get("source_url"):
                continue
            shared = left[1] & right[1]
            if len(shared) < 2:
                continue
            reasons = []
            if left[2] and right[2] and left[2] != right[2]:
                reasons.append("numeric_literals_differ")
            if left[3] != right[3]:
                reasons.append("negation_marker_differs")
            if reasons:
                conflicts.append({
                    "left_url": left[0].get("source_url", ""),
                    "right_url": right[0].get("source_url", ""),
                    "shared_terms": ", ".join(sorted(shared)[:8]),
                    "signals": ", ".join(reasons),
                    "note": "Heuristic disagreement candidate; human review required.",
                })
    return conflicts


def build_evidence_report(query: str, sources: list[FetchResult | dict]) -> dict:
    """Create a mechanical retrieval report and abstain when no text matches."""
    passages: list[dict[str, str]] = []
    for source in sources:
        if isinstance(source, FetchResult):
            passages.extend(source.evidence_passages)
        elif isinstance(source, dict):
            value = source.get("evidence_passages", [])
            if isinstance(value, list):
                passages.extend(item for item in value if isinstance(item, dict))
    query_terms = {word.lower() for word in re.findall(r"[A-Za-z]{3,}", query)}
    matched = [
        passage for passage in passages
        if query_terms & {word.lower() for word in re.findall(r"[A-Za-z]{3,}", passage.get("quote", ""))}
    ]
    return {
        "status": "retrieval_candidates_only" if matched else "insufficient_evidence",
        "query": query,
        "passages": matched,
        "possible_conflicts": candidate_contradictions(passages),
        "human_review_required": True,
        "factual_correctness_assessed": False,
    }


def fetch_evidence(url: str, max_chars: int = 4000) -> dict:
    """Fetch one source and return passage metadata with an explicit caveat."""
    result = fetch_url_metadata(url, max_chars=max_chars)
    return {
        "status": "retrieved_unverified" if result.success and result.evidence_passages else "insufficient_evidence",
        "source_url": result.url,
        "title": result.title,
        "fetched_at": result.fetched_at,
        "last_modified": result.last_modified,
        "heuristic_domain_score": result.heuristic_domain_score,
        "text": result.text,
        "evidence_passages": result.evidence_passages,
        "possible_conflicts": [],
        "factual_correctness_assessed": False,
        "note": "Extracted text and quoted passages require source/context review.",
    }


def fetch_url_metadata(url: str, max_chars: int = 4000) -> FetchResult:
    """Fetch/extract a URL and attach a best-effort date and heuristic score."""
    import urllib.request
    from urllib.parse import urlparse

    fetched_at = datetime.now(timezone.utc).isoformat()
    domain = urlparse(url).netloc.lower()
    domain_score = _heuristic_domain_score(domain)
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
    success = not text.startswith("Extraction failed")

    return FetchResult(
        url=url,
        title=None,
        text=text,
        last_modified=last_modified,
        heuristic_domain_score=domain_score,
        fetched_at=fetched_at,
        success=success,
        error=None if success else text,
        evidence_passages=extract_evidence_passages(text, url) if success else [],
    )


def format_source_tag(result: FetchResult) -> str:
    """
    Produce a source tag with URL, date, and heuristic domain-score metadata.
    It does not verify the source or resolve conflicting claims.
    """
    date = escape(result.last_modified or "unknown", quote=True)
    url = escape(result.url, quote=True)
    passages = "\n".join(
        "<quoted_passage>" + escape(item.get("quote", "")) + "</quoted_passage>"
        for item in result.evidence_passages
    )
    return (
        f'<source url="{url}" date="{date}" '
        f'domain_score="{result.heuristic_domain_score}" '
        'domain_score_type="heuristic_not_credibility">\n'
        f"{passages}\n"
        f"{escape(result.text)}\n</source>"
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ii_web.py \"search query\"")
        sys.exit(1)
    query = sys.argv[1]
    print(search_web(query, max_results=3))
