"""Tiny, dependency-free TF-IDF utilities.

The project is not allowed to pull in scikit-learn (RAM budget + dependency
list), so this module implements the small amount of vector maths that the
logic-tree solver and the Exocortex need:

* tokenisation,
* term frequency,
* inverse document frequency over a document collection,
* cosine similarity between sparse vectors,
* sentence ranking of a candidate set against a query.

Everything is pure Python and deterministic: the same inputs always produce
the same scores, which the determinism benchmark relies on.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable, Sequence

__all__ = [
    "tokenize",
    "term_frequency",
    "build_idf",
    "tfidf_vector",
    "cosine",
    "rank_against_query",
    "vector_to_dense",
]

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'’._+-]*")

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "of", "in", "on", "at", "to",
    "for", "with", "about", "into", "by", "from", "as", "is", "are", "was",
    "were", "be", "been", "being", "do", "does", "did", "have", "has", "had",
    "i", "you", "he", "she", "it", "we", "they", "them", "this", "that",
    "these", "those", "there", "what", "which", "who", "when", "where", "why",
    "how", "all", "any", "can", "will", "would", "should", "could", "not",
}


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    """Lower-case tokens; stop words removed unless ``keep_stopwords``."""
    tokens = [m.group(0).strip("._'-") for m in _TOKEN_RE.finditer((text or "").lower())]
    tokens = [t for t in tokens if t]
    if keep_stopwords:
        return tokens
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]


def term_frequency(tokens: Sequence[str]) -> Counter:
    """Raw term frequency counter."""
    return Counter(tokens)


def build_idf(documents: Iterable[Sequence[str]]) -> dict[str, float]:
    """Smoothed inverse document frequency over ``documents``."""
    docs = list(documents)
    total = len(docs)
    if total == 0:
        return {}
    document_frequency: Counter = Counter()
    for tokens in docs:
        document_frequency.update(set(tokens))
    idf: dict[str, float] = {}
    for term, count in document_frequency.items():
        idf[term] = math.log((1 + total) / (1 + count)) + 1.0
    return idf


def tfidf_vector(tokens: Sequence[str], idf: dict[str, float]) -> dict[str, float]:
    """L2-normalised sparse TF-IDF vector."""
    if not tokens:
        return {}
    counts = term_frequency(tokens)
    max_count = max(counts.values()) or 1
    vector = {
        term: (count / max_count) * idf.get(term, 1.0)
        for term, count in counts.items()
    }
    norm = math.sqrt(sum(value * value for value in vector.values()))
    if norm == 0:
        return vector
    return {term: value / norm for term, value in vector.items()}


def cosine(left: dict[str, float], right: dict[str, float]) -> float:
    """Cosine similarity of two sparse vectors."""
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(term, 0.0) for term, value in left.items())


def rank_against_query(
    sentences: Sequence[str],
    query: str,
    *,
    top_k: int = 3,
    min_score: float = 0.0,
) -> list[tuple[int, float, str]]:
    """Rank ``sentences`` against ``query``.

    Returns ``[(index, score, sentence), ...]`` sorted by descending score.
    The IDF table is built from the candidate sentences themselves, so a term
    that appears in every candidate carries no weight.
    """
    if not sentences:
        return []
    tokenised = [tokenize(sentence) for sentence in sentences]
    idf = build_idf(tokenised)
    query_vector = tfidf_vector(tokenize(query), idf)
    scored: list[tuple[int, float, str]] = []
    for index, tokens in enumerate(tokenised):
        score = cosine(tfidf_vector(tokens, idf), query_vector)
        if score > min_score:
            scored.append((index, score, sentences[index]))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[:top_k]


def vector_to_dense(vector: dict[str, float], vocabulary: Sequence[str]) -> list[float]:
    """Project a sparse vector onto a fixed vocabulary (for FAISS)."""
    return [float(vector.get(term, 0.0)) for term in vocabulary]
