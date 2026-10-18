"""Thin metadata wrapper around :mod:`vendor.kv_pager`.

It records rankings and nominal tiers only; it does not move a KV tensor between
RAM and VRAM or modify a model attention cache.
"""

from __future__ import annotations

from typing import Any, Iterable

from archive.vendor.kv_pager import KVCachePager, KVPageSummary


class KVPagingManager:
    def __init__(self, page_size: int = 64, state_manager: Any | None = None):
        self.pager = KVCachePager(page_size=page_size)
        self.state_manager = state_manager
        self._tiers: dict[str, str] = {}

    def index_key_vectors(self, vectors: Iterable[Any], *, prefix: str = "page") -> list[KVPageSummary]:
        summaries = self.pager.index_key_vectors(vectors, prefix=prefix)
        for summary in summaries:
            self._tiers[summary.page_id] = summary.tier
            if self.state_manager is not None:
                self.state_manager.record_kv_page(summary.page_id, summary.tier)
        return summaries

    def get_relevant_pages(self, query: Any, top_k: int = 4) -> list[KVPageSummary]:
        return self.pager.retrieve(query, top_k=top_k)

    def page_tier(self, page_id: str) -> str:
        if page_id not in self.pager.pages:
            raise KeyError(page_id)
        return self._tiers[page_id]

    def offload_page(self, page_id: str, kv_payload: Any) -> None:
        self.pager.offload_page(page_id, kv_payload)

    def fetch_page(self, page_id: str) -> Any:
        return self.pager.fetch_page(page_id)
