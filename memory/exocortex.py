"""Exocortex -- disk-backed paged memory for II.

What it is
----------
A transparent, inspectable store of *facts the user agreed to keep*, plus
episodes and tool-run bookkeeping.  Everything lives on disk (SQLite for the
records, a FAISS index for keyword retrieval) so the process stays inside the
project RAM budget.

What it is **not**
-----------------
* It does not profile the user in the background.  Profiling is **opt-in**:
  nothing is written until the user explicitly approves, both the profiling
  *feature* and each individual proposed item.
* It does not embed text with a neural model.  Retrieval uses TF-IDF keyword
  vectors projected into a FAISS index, which keeps the dependency footprint
  at zero extra model weights.  A neural re-ranker is listed as future work
  with an explicit size budget (see :data:`NEURAL_EMBEDDING_FUTURE_WORK`).
* The word "Infinite" is not used for this component anywhere: the memory is
  disk-backed and paged, and its capacity is bounded by the disk.

Layout
------
``<data_dir>/exocortex/exocortex.db``   -- SQLite (facts, episodes, tool runs)
``<data_dir>/exocortex/vector_index.faiss`` -- FAISS index (keyword vectors)
``<data_dir>/exocortex/vector_index.json``  -- vocabulary + idf + fallback vectors
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from reasoning.tfidf import build_idf, tfidf_vector, tokenize

__all__ = [
    "Exocortex",
    "ProfileItem",
    "propose_profile_items",
    "NEURAL_EMBEDDING_FUTURE_WORK",
    "DEFAULT_DATA_DIR",
]

DEFAULT_DATA_DIR = Path(
    os.environ.get("II_DATA_DIR", Path.home() / ".ii_exocortex")
).expanduser()

#: Explicitly *not* implemented in this phase, with the size budget that a
#: future implementation would have to respect.
NEURAL_EMBEDDING_FUTURE_WORK = {
    "feature": "neural re-ranking of recalled facts",
    "status": "not implemented",
    "budget": {
        "model": "all-MiniLM-L6-v2 (int8)",
        "approx_ram_mb": 45,
        "approx_disk_mb": 90,
        "note": (
            "Would add a model download and ~45 MB of resident memory; the "
            "2 GB RAM budget allows it, but the download is not acceptable "
            "for an offline-first tool, so TF-IDF keyword vectors are used "
            "instead."
        ),
    },
}


@dataclass
class ProfileItem:
    """A proposed (not yet approved) profile entry."""

    key: str
    value: str
    reason: str
    source: str = "conversation"

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "reason": self.reason,
            "source": self.source,
        }


# --------------------------------------------------------------------------- #
# Profiling proposals (opt-in, never silent)
# --------------------------------------------------------------------------- #

_PROFILE_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(?:i|we)\s+(?:prefer|like|always use|usually use|work with|"
            r"code in|build with)\s+([A-Za-z0-9+#._\- ]{2,40})",
            re.IGNORECASE,
        ),
        "preference",
    ),
    (
        re.compile(
            r"\bmy\s+(?:name|role|job|team|company|location|timezone|email)\s+"
            r"(?:is|=)\s+([^.;!?\n]{2,60})",
            re.IGNORECASE,
        ),
        "identity",
    ),
    (
        re.compile(
            r"\b(?:i|we)\s+(?:work on|am building|am working on|am responsible for)\s+"
            r"([^.;!?\n]{2,80})",
            re.IGNORECASE,
        ),
        "project",
    ),
]


def propose_profile_items(text: str) -> list[ProfileItem]:
    """Propose candidate profile items found in ``text``.

    The function only *proposes*.  Nothing is stored until the user approves
    each item explicitly (see :meth:`Exocortex.approve_profile_item`).
    """
    proposals: list[ProfileItem] = []
    seen: set[str] = set()
    for pattern, category in _PROFILE_RULES:
        for match in pattern.finditer(text or ""):
            value = " ".join(match.group(1).split()).strip(" .,;:")
            if not value or len(value) < 2:
                continue
            key = f"{category}:{value.lower()}"
            if key in seen:
                continue
            seen.add(key)
            proposals.append(ProfileItem(
                key=key,
                value=value,
                reason=f"explicit pattern match for {category} in the conversation",
                source="conversation",
            ))
    return proposals


CONSENT_UNKNOWN = "unknown"
CONSENT_GRANTED = "granted"
CONSENT_DENIED = "denied"


class Exocortex:
    """Disk-backed paged memory."""

    def __init__(
        self,
        data_dir: Optional[str | Path] = None,
        *,
        page_cache_size: int = 32,
    ):
        self.data_dir = Path(data_dir or DEFAULT_DATA_DIR).expanduser()
        self.exocortex_dir = self.data_dir / "exocortex"
        self.exocortex_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.exocortex_dir / "exocortex.db"
        self.index_path = self.exocortex_dir / "vector_index.faiss"
        self.meta_path = self.exocortex_dir / "vector_index.json"
        self.page_cache_size = max(1, int(page_cache_size))
        self._page_cache: "OrderedDict[str, str]" = OrderedDict()
        self._init_db()

    # -- database --------------------------------------------------------- #

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    source_url TEXT,
                    created_at REAL NOT NULL,
                    approved INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    summary TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    ended_at REAL
                );
                CREATE TABLE IF NOT EXISTS tool_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_name TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    ran_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def _get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def _set_meta(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # -- consent ---------------------------------------------------------- #

    def consent_state(self) -> str:
        """Return ``"unknown"``, ``"granted"`` or ``"denied"``."""
        return self._get_meta("profiling_consent", CONSENT_UNKNOWN) or CONSENT_UNKNOWN

    def set_consent(self, granted: bool) -> str:
        """Record the user's explicit decision about memory/profiling."""
        state = CONSENT_GRANTED if granted else CONSENT_DENIED
        self._set_meta("profiling_consent", state)
        if not granted:
            # Revoking consent removes previously stored profile items.
            with self._connect() as connection:
                connection.execute("DELETE FROM facts WHERE approved = 1")
        self._rebuild_index()
        return state

    def request_consent(self, ask: Optional[Callable[[str], bool]] = None) -> str:
        """Ask the user once whether II may keep memory.

        ``ask`` receives the question text and must return a boolean.  When
        no callback is supplied the state is *not* changed -- consent is never
        inferred.
        """
        if ask is None:
            return self.consent_state()
        granted = bool(ask(
            "II can remember facts you explicitly approve (stored on this "
            "machine, deletable at any time). Allow memory?"
        ))
        return self.set_consent(granted)

    # -- facts ------------------------------------------------------------ #

    def remember(
        self,
        key: str,
        value: str,
        source_url: Optional[str] = None,
        *,
        approved: bool = True,
    ) -> bool:
        """Store one fact.

        Returns ``False`` when memory is switched off (no consent) -- the
        Exocortex never writes silently.
        """
        if not approved:
            return False
        if self.consent_state() != CONSENT_GRANTED:
            return False
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO facts(key, value, source_url, created_at, approved) "
                "VALUES(?, ?, ?, ?, 1) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "source_url = excluded.source_url, created_at = excluded.created_at",
                (key, value, source_url, time.time()),
            )
        self._rebuild_index()
        return True

    def forget(self, key: str) -> bool:
        """Delete one stored fact.  Returns ``True`` when something was removed."""
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM facts WHERE key = ?", (key,))
            removed = cursor.rowcount > 0
        if removed:
            self._rebuild_index()
        return removed

    def forget_all(self) -> int:
        """Delete every stored fact.  Returns the number of removed rows."""
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM facts")
            removed = cursor.rowcount or 0
        self._rebuild_index()
        return removed

    def what_i_remember(self) -> list[dict]:
        """Return every stored fact (the visible "what I remember" view)."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT key, value, source_url, created_at FROM facts "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def approve_profile_item(self, item: ProfileItem | dict) -> bool:
        """Store one *explicitly approved* profile item.

        Approval is per item: nothing is written unless the caller passes an
        item the user agreed to.
        """
        if isinstance(item, dict):
            item = ProfileItem(
                key=item.get("key", ""),
                value=item.get("value", ""),
                reason=item.get("reason", ""),
                source=item.get("source", "conversation"),
            )
        if not item.key or not item.value:
            return False
        return self.remember(item.key, item.value, item.source)

    def recall(self, query: str, top_k: int = 5) -> list[dict]:
        """Return the facts most similar to ``query`` (keyword vectors)."""
        facts = self.what_i_remember()
        if not facts:
            return []
        vocabulary, idf = self._load_vocabulary()
        if not vocabulary:
            return []
        query_vector = tfidf_vector(tokenize(query), idf)
        if not query_vector:
            return []
        vectors = self._fact_vectors(facts, vocabulary, idf)
        scored: list[tuple[float, dict]] = []
        for fact, vector in zip(facts, vectors):
            score = sum(
                value * query_vector.get(term, 0.0)
                for term, value in vector.items()
            )
            scored.append((score, fact))
        scored.sort(key=lambda item: (-item[0], item[1]["key"]))
        results = []
        for score, fact in scored[:top_k]:
            entry = dict(fact)
            entry["score"] = round(float(score), 4)
            results.append(entry)
        return results

    def get_user_context(self) -> dict:
        """Return the remembered context.

        The result is **empty unless the user opted in**.  This is the only
        function the router uses to read memory, which keeps the "no silent
        profiling" rule enforceable in one place.
        """
        if self.consent_state() != CONSENT_GRANTED:
            return {}
        return {
            fact["key"]: fact["value"]
            for fact in self.what_i_remember()
        }

    # -- episodes + tool runs --------------------------------------------- #

    def start_episode(self, summary: str) -> int:
        """Record the start of an episode; returns the episode id."""
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO episodes(summary, started_at) VALUES(?, ?)",
                (summary, time.time()),
            )
            return int(cursor.lastrowid or 0)

    def end_episode(self, episode_id: int, summary: Optional[str] = None) -> None:
        with self._connect() as connection:
            if summary:
                connection.execute(
                    "UPDATE episodes SET ended_at = ?, summary = ? WHERE id = ?",
                    (time.time(), summary, episode_id),
                )
            else:
                connection.execute(
                    "UPDATE episodes SET ended_at = ? WHERE id = ?",
                    (time.time(), episode_id),
                )

    def episodes(self, limit: int = 20) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, summary, started_at, ended_at FROM episodes "
                "ORDER BY started_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_tool_run(self, tool_name: str, success: bool, latency_ms: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO tool_runs(tool_name, success, latency_ms, ran_at) "
                "VALUES(?, ?, ?, ?)",
                (tool_name, 1 if success else 0, int(latency_ms), time.time()),
            )

    def tool_runs(self, limit: int = 50) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT tool_name, success, latency_ms, ran_at FROM tool_runs "
                "ORDER BY ran_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- page cache ------------------------------------------------------- #

    def cached_page(self, url: str) -> Optional[str]:
        """Return a cached page body (LRU) or ``None``."""
        if url in self._page_cache:
            self._page_cache.move_to_end(url)
            return self._page_cache[url]
        return None

    def cache_page(self, url: str, text: str) -> None:
        """Store a fetched page in the in-memory LRU cache."""
        self._page_cache[url] = text
        self._page_cache.move_to_end(url)
        while len(self._page_cache) > self.page_cache_size:
            self._page_cache.popitem(last=False)

    def clear_page_cache(self) -> None:
        self._page_cache.clear()

    # -- vector index ----------------------------------------------------- #

    def _load_vocabulary(self) -> tuple[list[str], dict[str, float]]:
        if not self.meta_path.exists():
            return [], {}
        try:
            payload = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except Exception:
            return [], {}
        return list(payload.get("vocabulary", [])), dict(payload.get("idf", {}))

    def _fact_vectors(
        self,
        facts: list[dict],
        vocabulary: list[str],
        idf: dict[str, float],
    ) -> list[dict[str, float]]:
        tokenised = [tokenize(f"{f['key']} {f['value']}") for f in facts]
        return [tfidf_vector(tokens, idf) for tokens in tokenised]

    def _rebuild_index(self) -> None:
        """Rebuild the on-disk keyword index over the stored facts."""
        facts = self.what_i_remember()
        if not facts:
            self.meta_path.write_text(
                json.dumps({"vocabulary": [], "idf": {}, "vectors": []}),
                encoding="utf-8",
            )
            if self.index_path.exists():
                try:
                    self.index_path.unlink()
                except OSError:
                    pass
            return

        tokenised = [tokenize(f"{f['key']} {f['value']}") for f in facts]
        vocabulary = sorted({term for tokens in tokenised for term in tokens})
        idf = build_idf(tokenised)
        vectors = self._fact_vectors(facts, vocabulary, idf)
        dense = [
            [float(vector.get(term, 0.0)) for term in vocabulary] for vector in vectors
        ]
        self.meta_path.write_text(
            json.dumps(
                {
                    "vocabulary": vocabulary,
                    "idf": idf,
                    "vectors": vectors,
                    "backend": "tfidf_keyword",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self._write_faiss(dense)

    def _write_faiss(self, dense: list[list[float]]) -> None:
        """Write the FAISS index to disk (best effort, never fatal)."""
        try:
            import faiss  # type: ignore  # lazy import: heavy library
        except Exception:
            return
        try:
            if not dense:
                return
            matrix = faiss.IndexFlatIP(len(dense[0]))
            matrix.add(faiss.np.array(dense, dtype="float32"))
            faiss.write_index(matrix, str(self.index_path))
        except Exception:
            # A failed index write must not break memory storage; the JSON
            # sidecar still carries the vectors used by ``recall``.
            return

    def index_backend(self) -> str:
        """Return ``"faiss"`` or ``"json-fallback"`` for the vector index."""
        return "faiss" if self.index_path.exists() else "json-fallback"

    # -- transparency ----------------------------------------------------- #

    def describe(self) -> dict:
        """Machine-readable description of what the Exocortex stores."""
        return {
            "name": "Exocortex (disk-backed paged memory)",
            "storage": {
                "database": str(self.db_path),
                "vector_index": str(self.index_path),
                "index_backend": self.index_backend(),
            },
            "tables": ["facts", "episodes", "tool_runs", "meta"],
            "profiling": {
                "opt_in": True,
                "consent_state": self.consent_state(),
                "silent_profiling": False,
            },
            "fact_count": len(self.what_i_remember()),
            "neural_embeddings": NEURAL_EMBEDDING_FUTURE_WORK,
        }
