# SPDX-License-Identifier: MIT
# Conceptual references: THUNLP/InfLLM (MIT) and mit-han-lab/Quest (MIT); no source copied.
"""Interface and metadata for future block-sparse KV-cache paging.

The page summaries and ranking are usable metadata primitives. Actual transfer
between llama.cpp/llama-cpp-python GPU memory and system RAM is intentionally
unimplemented: this repository has no supported access to those private cache
internals. No claim of cache compression, eviction, or context extension is made.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

PAGE_SIZE_TOKENS = 64


def _flat(values: Any) -> list[float]:
    if hasattr(values, "detach"):
        values = values.detach().cpu().tolist()
    elif hasattr(values, "tolist") and not isinstance(values, (list, tuple)):
        values = values.tolist()
    output: list[float] = []

    def visit(value: Any) -> None:
        if isinstance(value, (list, tuple)):
            for item in value:
                visit(item)
        else:
            output.append(float(value))

    visit(values)
    return output


@dataclass
class KVPageSummary:
    page_id: str
    token_start: int
    token_end: int  # exclusive
    minimum: list[float]
    maximum: list[float]
    tier: str = "ram_metadata"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class KVCachePager:
    """Index page-level key summaries and rank candidate pages for a query."""

    def __init__(self, page_size: int = PAGE_SIZE_TOKENS):
        if page_size != PAGE_SIZE_TOKENS:
            raise ValueError("The current interface uses fixed 64-token pages.")
        self.page_size = page_size
        self.pages: dict[str, KVPageSummary] = {}

    def index_key_vectors(
        self,
        key_vectors: Iterable[Any],
        *,
        prefix: str = "page",
    ) -> list[KVPageSummary]:
        """Create min/max-per-channel summaries for consecutive 64-token pages."""
        rows = [_flat(vector) for vector in key_vectors]
        if not rows:
            return []
        width = len(rows[0])
        if any(len(row) != width for row in rows):
            raise ValueError("All key vectors must have the same channel width.")
        summaries: list[KVPageSummary] = []
        for page_number, start in enumerate(range(0, len(rows), self.page_size)):
            page_rows = rows[start : start + self.page_size]
            minimum = [min(row[channel] for row in page_rows) for channel in range(width)]
            maximum = [max(row[channel] for row in page_rows) for channel in range(width)]
            summary = KVPageSummary(
                page_id=f"{prefix}-{page_number:06d}",
                token_start=start,
                token_end=start + len(page_rows),
                minimum=minimum,
                maximum=maximum,
            )
            self.pages[summary.page_id] = summary
            summaries.append(summary)
        return summaries

    @staticmethod
    def _upper_bound_score(query: Sequence[float], page: KVPageSummary) -> float:
        """Return the maximum dot product possible inside the page's box."""
        if len(query) != len(page.minimum):
            raise ValueError("Query width does not match page-summary channel width.")
        return sum(
            float(value) * (high if value >= 0 else low)
            for value, low, high in zip(query, page.minimum, page.maximum)
        )

    def retrieve(self, query: Any, top_k: int = 4) -> list[KVPageSummary]:
        """Rank pages by the box's upper-bound dot product with ``query``."""
        if top_k <= 0:
            return []
        vector = _flat(query)
        ranked = sorted(
            self.pages.values(),
            key=lambda page: (-self._upper_bound_score(vector, page), page.page_id),
        )
        return ranked[:top_k]

    def offload_page(self, page_id: str, kv_payload: Any) -> None:
        """TODO: requires a supported llama.cpp KV-cache transfer API."""
        if page_id not in self.pages:
            raise KeyError(page_id)
        raise NotImplementedError(
            "KV payload paging is unavailable until llama.cpp exposes stable cache internals."
        )

    def fetch_page(self, page_id: str) -> Any:
        """TODO: restore a page from system RAM into the model's active cache."""
        if page_id not in self.pages:
            raise KeyError(page_id)
        raise NotImplementedError(
            "KV page fetch is unavailable until llama.cpp exposes stable cache internals."
        )
