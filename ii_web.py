"""Internet-as-Brain web layer: multi-backend search with citation tracking.

All backends are free and require NO API keys:

  1. DuckDuckGo HTML (free, no key) — primary fallback
  2. Public SearXNG instances (free, no key) — metadata-rich results
  3. Wikipedia API (free, no key) — encyclopedia facts
  4. Crossref API (free, no key) — academic papers

Every search result gets a stable citation ID. The model must cite sources
as [1], [2], etc. Source text is extracted via Trafilatura and stored for
later claim verification.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from html import escape
from datetime import datetime, timezone
from typing import Optional

import trafilatura
from duckduckgo_search import DDGS

try:
    import requests  # type: ignore
except ImportError:
    requests = None  # type: ignore


# --------------------------------------------------------------------------- #
# Search result cache (300-second TTL for speed)
# --------------------------------------------------------------------------- #
_search_cache: dict[str, tuple[str, float]] = {}


# --------------------------------------------------------------------------- #
# Public SearXNG instances (no API key, community-run)
# If one is down, the next is tried automatically.
# --------------------------------------------------------------------------- #
SEARXNG_INSTANCES = [
    "https://searx.be",
    "https://searx.tiekoetter.com",
    "https://search.bus-hit.me",
    "https://searxng.site",
    "https://opnxng.com",
    "https://searx.fmac.xyz",
    "https://search.inetol.net",
    "https://searx.foobar.vip",
]


# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #
@dataclass
class SearchResult:
    """A single search result with citation metadata."""
    citation_id: str   # e.g. "[1]"
    title: str
    link: str
    snippet: str
    source: str        # "duckduckgo" | "searxng" | "wikipedia" | "crossref"
    fetched_at: str
    authority_score: int


@dataclass
class FetchResult:
    """Fetched + extracted page content with evidence passages."""
    url: str
    title: Optional[str]
    text: str
    last_modified: Optional[str]
    domain_score: int
    fetched_at: str
    success: bool
    error: Optional[str] = None
    evidence_passages: list[dict[str, str]] = field(default_factory=list)


@dataclass
class SearchResponse:
    """Aggregated search response across backends."""
    query: str
    results: list[SearchResult]
    total_found: int
    backends_used: list[str]
    citation_map: dict[str, dict]  # citation_id -> {title, link, snippet}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _make_citation_id(index: int) -> str:
    return f"[{index}]"


def _heuristic_domain_score(domain: str) -> int:
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
    if re.search(r"(cheap|best|top|review|202[0-9])\.com$", domain):
        score -= 10
    return max(0, min(100, score))


def _extract_text_evidence(text: str, source_url: str, max_passages: int = 5,
                           max_chars: int = 600) -> list[dict[str, str]]:
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


# --------------------------------------------------------------------------- #
# Backend 1: DuckDuckGo HTML (free, no key)
# --------------------------------------------------------------------------- #
def _search_duckduckgo(query: str, max_results: int) -> list[SearchResult]:
    results: list[SearchResult] = []
    try:
        with DDGS() as ddgs:
            for i, r in enumerate(ddgs.text(query, region="wt-wt",
                                           max_results=max_results), 1):
                results.append(SearchResult(
                    citation_id=_make_citation_id(i),
                    title=r.get("title", ""),
                    link=r.get("href", ""),
                    snippet=r.get("body", ""),
                    source="duckduckgo",
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    authority_score=_heuristic_domain_score(
                        r.get("href", "").split("//")[-1].split("/")[0]
                    ),
                ))
    except Exception:
        pass
    return results


# --------------------------------------------------------------------------- #
# Backend 2: Public SearXNG instances (free, no key)
# --------------------------------------------------------------------------- #
def _search_searxng(query: str, max_results: int) -> list[SearchResult] | None:
    if requests is None:
        return None
    for base in SEARXNG_INSTANCES:
        try:
            resp = requests.get(
                base.rstrip("/") + "/search",
                params={"q": query, "format": "json", "language": "en"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            results: list[SearchResult] = []
            for i, item in enumerate(data.get("results", [])[:max_results], 1):
                results.append(SearchResult(
                    citation_id=_make_citation_id(i),
                    title=item.get("title", ""),
                    link=item.get("url", ""),
                    snippet=item.get("content", ""),
                    source="searxng",
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    authority_score=_heuristic_domain_score(
                        item.get("url", "").split("//")[-1].split("/")[0]
                    ),
                ))
            if results:
                return results
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------- #
# Backend 3: Wikipedia API (free, no key)
# --------------------------------------------------------------------------- #
def _search_wikipedia(query: str, max_results: int) -> list[SearchResult]:
    if requests is None:
        return []
    results: list[SearchResult] = []
    try:
        # Search Wikipedia for page titles
        resp = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": max_results,
                "format": "json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        for i, item in enumerate(data.get("query", {}).get("search", []), 1):
            title = item.get("title", "")
            page_id = item.get("pageid", 0)
            link = f"https://en.wikipedia.org/?curid={page_id}"
            results.append(SearchResult(
                citation_id=_make_citation_id(i),
                title=title,
                link=link,
                snippet=item.get("snippet", "").replace("<span class=\"searchmatch\">", "").replace("</span>", ""),
                source="wikipedia",
                fetched_at=datetime.now(timezone.utc).isoformat(),
                authority_score=85,  # Wikipedia is generally reliable
            ))
    except Exception:
        pass
    return results


# --------------------------------------------------------------------------- #
# Backend: Brave Search (free, no key required for basic tier)
# --------------------------------------------------------------------------- #
def _search_brave(query: str, max_results: int) -> list[SearchResult]:
    results: list[SearchResult] = []
    try:
        if requests is not None:
            resp = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": "",  # Free tier: empty or basic
                    "User-Agent": "II-Agent/1.0",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                for i, item in enumerate(data.get("web", {}).get("results", [])[:max_results], 1):
                    results.append(SearchResult(
                        citation_id=_make_citation_id(i),
                        title=item.get("title", ""),
                        link=item.get("url", ""),
                        snippet=item.get("description", ""),
                        source="brave",
                        fetched_at=datetime.now(timezone.utc).isoformat(),
                        authority_score=85,
                    ))
    except Exception:
        pass
    return results


# --------------------------------------------------------------------------- #
# Backend: Headless browser (Playwright) — pulls live info from pages
# --------------------------------------------------------------------------- #
def _search_headless(query: str, max_results: int) -> list[SearchResult]:
    results: list[SearchResult] = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            # Use a simple search URL construction for headless pull
            search_url = f"https://duckduckgo.com/html/?q={query.replace(' ', '+')}"
            page.goto(search_url, timeout=15000)
            html = page.content()
            browser.close()
            # Quick extract: find result titles/links from DuckDuckGo HTML
            import re as _re
            matches = _re.findall(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, _re.DOTALL)
            for i, (link, title_raw) in enumerate(matches[:max_results], 1):
                title = _re.sub(r"<.*?>", "", title_raw).strip()
                if title and link.startswith("http"):
                    results.append(SearchResult(
                        citation_id=_make_citation_id(i),
                        title=title[:200],
                        link=link,
                        snippet="Live headless browser extraction",
                        source="headless",
                        fetched_at=datetime.now(timezone.utc).isoformat(),
                        authority_score=80,
                    ))
    except Exception:
        pass
    return results


# --------------------------------------------------------------------------- #
# Backend: GitHub Search (public API, no key required for basic search)
# --------------------------------------------------------------------------- #
def _search_github(query: str, max_results: int) -> list[SearchResult]:
    if requests is None:
        return []
    results: list[SearchResult] = []
    try:
        # GitHub public search endpoint (no auth required for basic search)
        resp = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": query, "sort": "stars", "order": "desc", "per_page": max_results},
            timeout=15,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "II-Agent/1.0"},
        )
        if resp.status_code == 200:
            data = resp.json()
            for i, item in enumerate(data.get("items", [])[:max_results], 1):
                results.append(SearchResult(
                    citation_id=_make_citation_id(i),
                    title=item.get("full_name", item.get("name", "")),
                    link=item.get("html_url", ""),
                    snippet=item.get("description", "")[:300],
                    source="github",
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    authority_score=95,
                ))
    except Exception:
        pass
    return results


# --------------------------------------------------------------------------- #
# Backend 4: Crossref (free, no key — academic papers)
# --------------------------------------------------------------------------- #
def _search_crossref(query: str, max_results: int) -> list[SearchResult]:
    if requests is None:
        return []
    results: list[SearchResult] = []
    try:
        resp = requests.get(
            "https://api.crossref.org/works",
            params={
                "query": query,
                "rows": max_results,
                "select": "DOI,title,author,published,URL,abstract",
            },
            timeout=15,
            headers={"User-Agent": "II-Agent/1.0 (mailto:research@iimodel.local)"},
        )
        resp.raise_for_status()
        data = resp.json()
        for i, item in enumerate(data.get("message", {}).get("items", []), 1):
            title = item.get("title", [""])[0] if item.get("title") else ""
            doi = item.get("DOI", "")
            link = f"https://doi.org/{doi}" if doi else item.get("URL", "")
            results.append(SearchResult(
                citation_id=_make_citation_id(i),
                title=title,
                link=link,
                snippet=item.get("abstract", "")[:300] if item.get("abstract") else "",
                source="crossref",
                fetched_at=datetime.now(timezone.utc).isoformat(),
                authority_score=90,  # Academic papers are high authority
            ))
    except Exception:
        pass
    return results


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def search_web(query: str, max_results: int = 5,
               region: str = "wt-wt",
               require_citations: bool = True) -> str:
    """
    Search the web using free backends (no API keys required).

    Priority: DuckDuckGo -> SearXNG -> Wikipedia -> Crossref.
    Results are merged and de-duplicated by URL.
    Returns JSON with citation-tagged results.
    """
    all_results: list[SearchResult] = []
    backends_used: list[str] = []
    seen_links: set[str] = set()
    # Cache check (300s TTL)
    import time
    cache_key = query.lower().strip()
    cached = _search_cache.get(cache_key)
    if cached:
        result_text, cached_time = cached
        if time.time() - cached_time < 300:
            return result_text
    is_repo_query = any(k in query.lower() for k in ["github", "repo", "reverse engineering", "trending"])

    # Priority order: Brave (fast, rich) -> SearXNG (meta) -> DuckDuckGo (site:github.com for repos) -> GitHub API -> Headless (live) -> Wikipedia -> Crossref (academic only)
    # Brave first (fast free tier, best snippets)
    brave_results = _search_brave(query, max_results)
    if brave_results:
        for r in brave_results:
            if r.link not in seen_links:
                all_results.append(r)
                seen_links.add(r.link)
        backends_used.append("brave")

    # SearXNG (meta-aggregator)
    remaining = max_results - len(all_results)
    sx_results = _search_searxng(query, remaining) if remaining > 0 else None
    if sx_results:
        for r in sx_results:
            if r.link not in seen_links:
                all_results.append(r)
                seen_links.add(r.link)
        backends_used.append("searxng")

    # DuckDuckGo (fill remaining, with site:github.com for repo queries)
    remaining = max_results - len(all_results)
    if remaining > 0:
        ddg_query = query
        if is_repo_query:
            ddg_query = f"{query} site:github.com"
        ddg_results = _search_duckduckgo(ddg_query, remaining)
        if ddg_results:
            for r in ddg_results:
                if r.link not in seen_links:
                    all_results.append(r)
                    seen_links.add(r.link)
            backends_used.append("duckduckgo")

    # GitHub API (repo/code queries — direct results)
    remaining = max_results - len(all_results)
    if is_repo_query and remaining > 0:
        gh_results = _search_github(query, remaining)
        if gh_results:
            for r in gh_results:
                if r.link not in seen_links:
                    all_results.append(r)
                    seen_links.add(r.link)
            backends_used.append("github")

    # Wikipedia (known facts)
    remaining = max_results - len(all_results)
    if remaining > 0:
        wiki_results = _search_wikipedia(query, remaining)
        if wiki_results:
            for r in wiki_results:
                if r.link not in seen_links:
                    all_results.append(r)
                    seen_links.add(r.link)
            backends_used.append("wikipedia")

    # Headless browser (live page extraction — best for current/live info)
    remaining = max_results - len(all_results)
    if remaining > 0:
        headless_results = _search_headless(query, remaining)
        if headless_results:
            for r in headless_results:
                if r.link not in seen_links:
                    all_results.append(r)
                    seen_links.add(r.link)
            backends_used.append("headless")

    # Crossref (academic papers only — skip for repo/viral/search queries to avoid noise)
    remaining = max_results - len(all_results)
    if remaining > 0 and not is_repo_query:
        # Only use Crossref for clearly academic queries
        is_academic = any(k in query.lower() for k in ["study", "paper", "research", "doi", "academic", "journal", "publication"])
        if is_academic:
            cr_results = _search_crossref(query, remaining)
            if cr_results:
                for r in cr_results:
                    if r.link not in seen_links:
                        all_results.append(r)
                        seen_links.add(r.link)
                backends_used.append("crossref")

    # Fallback: try original query with site:github.com for repo queries, then shorter keywords
    if not all_results:
        # Try direct github-targeted DDG search first
        ddg_retry = _search_duckduckgo(f"{query} site:github.com", max_results)
        if ddg_retry:
            for r in ddg_retry:
                if r.link not in seen_links:
                    all_results.append(r)
                    seen_links.add(r.link)
            backends_used.append("duckduckgo")
        else:
            # Last resort: single keywords
            keywords = [w for w in query.split() if len(w) > 2 and w.lower() not in ["the", "and", "for", "now", "what", "is", "that"]]
            for kw in keywords[:2]:
                for backend_fn in [_search_duckduckgo, _search_github, _search_searxng, _search_wikipedia]:
                    retry = backend_fn(kw, max_results)
                    if retry:
                        for r in retry:
                            if r.link not in seen_links:
                                all_results.append(r)
                                seen_links.add(r.link)
                        backends_used.append(r.source)
                if all_results:
                    break

    # Truncate to max_results
    all_results = all_results[:max_results]

    # Re-number citations sequentially
    citation_map: dict[str, dict] = {}
    for i, r in enumerate(all_results, 1):
        r.citation_id = _make_citation_id(i)
        citation_map[r.citation_id] = {
            "title": r.title,
            "link": r.link,
            "snippet": r.snippet,
            "source": r.source,
            "authority_score": r.authority_score,
        }

    payload = SearchResponse(
        query=query,
        results=all_results,
        total_found=len(all_results),
        backends_used=backends_used,
        citation_map=citation_map,
    )

    # Build a model-ready text block with citation instructions
    lines = [
        f"SEARCH RESULTS for: {query}",
        f"Backends used: {', '.join(backends_used)}",
        f"Total results: {len(all_results)}",
        "",
        "CITATION RULES (MANDATORY):",
        "  - Cite sources as [1], [2], [3] etc. matching citation_id.",
        "  - Every factual claim from the web MUST have a citation.",
        "  - If sources conflict, say so and cite both.",
        "  - If no source supports a claim, say 'insufficient evidence'.",
        "  - Do not invent citation IDs not returned by the search tool.",
        "",
    ]
    for r in all_results:
        lines.append(
            f"{r.citation_id} {r.title}\n"
            f"    URL: {r.link}\n"
            f"    Source: {r.source} | Authority: {r.authority_score}/100\n"
            f"    Snippet: {r.snippet}\n"
        )
    lines.append("")
    lines.append("RAW_JSON_START")
    lines.append(json.dumps(
        {"results": [r.__dict__ for r in all_results],
         "citation_map": citation_map,
         "backends_used": backends_used},
        ensure_ascii=False, indent=2,
    ))
    lines.append("RAW_JSON_END")

    result_text = "\n".join(lines)
    # Cache result (300s TTL)
    _search_cache[cache_key] = (result_text, time.time())
    return result_text


# --------------------------------------------------------------------------- #
# Fetch + extract with evidence tracking
# --------------------------------------------------------------------------- #
def fetch_url_metadata(url: str, max_chars: int = 4000) -> FetchResult:
    """Fetch/extract a URL and attach metadata + evidence passages."""
    import urllib.request
    from urllib.parse import urlparse

    fetched_at = datetime.now(timezone.utc).isoformat()
    domain = urlparse(url).netloc.lower()
    domain_score = _heuristic_domain_score(domain)
    last_modified: Optional[str] = None

    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as resp:
            lm = resp.headers.get("Last-Modified")
            if lm:
                last_modified = lm
    except Exception:
        pass

    text = read_url(url, max_chars=max_chars)
    success = not text.startswith("Extraction failed")

    return FetchResult(
        url=url,
        title=None,
        text=text,
        last_modified=last_modified,
        domain_score=domain_score,
        fetched_at=fetched_at,
        success=success,
        error=None if success else text,
        evidence_passages=_extract_text_evidence(text, url) if success else [],
    )


def read_url(url: str, max_chars: int = 4000) -> str:
    """Fetch a URL and extract clean text via Trafilatura."""
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
    except Exception as exc:
        return f"Extraction failed: {exc}"


# --------------------------------------------------------------------------- #
# Research engine: multi-step loop with citation enforcement
# --------------------------------------------------------------------------- #
class ResearchEngine:
    """
    Orchestrates: search -> fetch -> verify -> synthesize -> cite.
    All backends are free; no API keys required.
    """

    def __init__(self, max_search_results: int = 5,
                 max_fetch_per_query: int = 3,
                 max_rounds: int = 3):
        self.max_search_results = max_search_results
        self.max_fetch_per_query = max_fetch_per_query
        self.max_rounds = max_rounds

    def research(self, query: str) -> dict:
        """Run: search -> fetch -> extract evidence -> check contradictions."""
        all_sources: dict[str, dict] = {}
        all_passages: list[dict[str, str]] = []
        search_log: list[dict] = []

        # Round 1: Search
        search_text = search_web(query, max_results=self.max_search_results)
        search_json = self._extract_json_from_search(search_text)
        results = search_json.get("results", [])
        citation_map = search_json.get("citation_map", {})

        for r in results:
            cid = r.get("citation_id", "[?]")
            all_sources[cid] = {
                "title": r.get("title", ""),
                "link": r.get("link", ""),
                "snippet": r.get("snippet", ""),
                "source": r.get("source", ""),
                "authority_score": r.get("authority_score", 0),
            }

        search_log.append({
            "round": 1,
            "query": query,
            "results": len(results),
            "backends": search_json.get("backends_used", []),
        })

        # Round 2: Fetch top results for full text
        fetch_count = 0
        for r in results[:self.max_fetch_per_query]:
            url = r.get("link", "")
            if not url:
                continue
            fetched = fetch_url_metadata(url)
            if fetched.success:
                for p in fetched.evidence_passages:
                    p["citation_id"] = r.get("citation_id", "[?]")
                    all_passages.append(p)
                fetch_count += 1

        search_log.append({
            "round": 2,
            "action": "fetch_full_text",
            "fetched": fetch_count,
        })

        # Round 3: Check contradictions across sources
        conflicts = candidate_contradictions(
            [{"source_url": p.get("source_url", ""), "quote": p.get("quote", "")}
             for p in all_passages]
        )

        return {
            "query": query,
            "sources": all_sources,
            "passages": all_passages,
            "conflicts": conflicts,
            "search_log": search_log,
            "total_sources": len(all_sources),
            "total_passages": len(all_passages),
        }

    @staticmethod
    def _extract_json_from_search(search_text: str) -> dict:
        start = search_text.find("RAW_JSON_START")
        end = search_text.find("RAW_JSON_END")
        if start == -1 or end == -1:
            return {"results": [], "citation_map": {}, "backends_used": []}
        json_str = search_text[start + len("RAW_JSON_START"):end].strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return {"results": [], "citation_map": {}, "backends_used": []}


# --------------------------------------------------------------------------- #
# Convenience: research + answer drafting
# --------------------------------------------------------------------------- #
def research_and_answer(query: str) -> dict:
    """
    Research a query and return a structured answer draft with citations.

    Returns dict with: query, answer_draft, sources, passages, conflicts,
    search_log, total_sources, total_passages, verification_status.
    """
    engine = ResearchEngine()
    result = engine.research(query)

    source_lines = []
    for cid, info in result["sources"].items():
        source_lines.append(
            f"{cid} {info['title']} — {info['link']} (authority: {info['authority_score']}/100)"
        )

    answer_draft = (
        f"RESEARCH QUERY: {query}\n"
        f"SOURCES FOUND: {result['total_sources']}\n"
        f"PASSAGES EXTRACTED: {result['total_passages']}\n"
        f"CONFLICTS DETECTED: {len(result['conflicts'])}\n\n"
        f"SOURCES:\n" + "\n".join(source_lines) + "\n\n"
        f"PASSAGES:\n" +
        "\n".join(
            f"[{p.get('citation_id', '[?]')}] {p.get('quote', '')}"
            for p in result["passages"][:20]
        ) + "\n\n"
        f"INSTRUCTIONS:\n"
        f"  - Answer using ONLY the sources above.\n"
        f"  - Cite every factual claim with [1], [2], etc.\n"
        f"  - If sources conflict, report the conflict and cite both sides.\n"
        f"  - If no source supports a claim, say 'insufficient evidence'.\n"
        f"  - Do not invent citations not in the source list.\n"
    )

    return {
        "query": query,
        "answer_draft": answer_draft,
        "sources": result["sources"],
        "passages": result["passages"],
        "conflicts": result["conflicts"],
        "search_log": result["search_log"],
        "total_sources": result["total_sources"],
        "total_passages": result["total_passages"],
        "verification_status": "unverified",
    }


# --------------------------------------------------------------------------- #
# Citation verification helper
# --------------------------------------------------------------------------- #
def _extract_citations(text: str) -> list[str]:
    return re.findall(r"\[(\d+)\]", text)


def verify_answer_citations(answer_text: str,
                            sources: dict[str, dict]) -> dict:
    """
    Check that every [n] in text maps to a real citation ID.
    """
    used = set(_extract_citations(answer_text))
    available = set(sources.keys())
    missing = sorted(used - available, key=lambda x: int(x.strip("[]")))
    unused = sorted(available - used, key=lambda x: int(x.strip("[]")))
    return {
        "verified": len(missing) == 0,
        "missing_citations": missing,
        "unused_sources": unused,
        "answer": answer_text,
    }


# --------------------------------------------------------------------------- #
# Legacy format_source_tag for backward compatibility
# --------------------------------------------------------------------------- #
def format_source_tag(result) -> str:
    """Produce a source tag with URL, date, and heuristic domain-score metadata."""
    date = escape(result.last_modified or "unknown", quote=True)
    url = escape(result.url, quote=True)
    passages = "\n".join(
        "<quoted_passage>" + escape(item.get("quote", "")) + "</quoted_passage>"
        for item in result.evidence_passages
    )
    return (
        f'<source url="{url}" date="{date}" '
        f'domain_score="{result.domain_score}" '
        f'domain_score_type="heuristic_not_credibility">\n'
        f"{passages}\n"
        f"{escape(result.text)}\n</source>"
    )


# --------------------------------------------------------------------------- #
# Re-export legacy names for backward compatibility
# --------------------------------------------------------------------------- #
SearchResult = SearchResult
FetchResult = FetchResult
format_source_tag = format_source_tag


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ii_web.py \"research query\"")
        sys.exit(1)
    query = sys.argv[1]
    result = research_and_answer(query)
    print(result["answer_draft"])
    print("\n--- SOURCES ---")
    for cid, info in result["sources"].items():
        print(f"{cid} {info['title']} — {info['link']}")
    print(f"\nConflicts: {len(result['conflicts'])}")
    for c in result["conflicts"]:
        print(f"  {c['left_url']} vs {c['right_url']}: {c['signals']}")
