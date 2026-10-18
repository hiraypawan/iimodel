"""Compatibility facade for root web/evidence helpers."""

from ingestion.ii_web import (
    FetchResult,
    SearchResult,
    build_evidence_report,
    candidate_contradictions,
    extract_evidence_passages,
    fetch_evidence,
    fetch_url_metadata,
    format_source_tag,
    read_url,
    search_web,
)

__all__ = [
    "FetchResult", "SearchResult", "build_evidence_report",
    "candidate_contradictions", "extract_evidence_passages", "fetch_evidence",
    "fetch_url_metadata", "format_source_tag", "read_url", "search_web",
]
