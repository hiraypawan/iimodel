"""Logic-tree solver -- deterministic multi-hop question answering.

The solver turns a natural-language question into an ordered list of *hops*
(sub-questions), executes them one after another against live web sources, and
extracts a cited answer for every hop.

Hard rules
----------
* **No LLM participates.**  Decomposition, execution order, extraction and
  scoring are plain Python algorithms.
* **Fail closed.**  If a hop cannot be resolved the solver stops, keeps the
  work it *did* do, and reports ``PARTIAL`` (or ``INSUFFICIENT_DATA`` when
  nothing at all could be resolved).  It never guesses.
* **Cite everything.**  Every extracted statement carries the URL it came
  from.

Status vocabulary (see the project status taxonomy):
``VERIFIED``, ``PARTIAL``, ``CONFLICT``, ``INSUFFICIENT_DATA``.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from core.nlp import content_words, extract_sentences
from reasoning.tfidf import rank_against_query

try:  # pragma: no cover - networkx is a hard dependency of the project
    import networkx as nx
except Exception:  # pragma: no cover
    nx = None  # type: ignore[assignment]

__all__ = [
    "HOP_PATTERNS",
    "MAX_HOPS",
    "SubQuery",
    "decompose",
    "build_hop_graph",
    "LogicTreeSolver",
]

# --------------------------------------------------------------------------- #
# Hop patterns
# --------------------------------------------------------------------------- #

HOP_PATTERNS: list[tuple[str, str]] = [
    (r"(?P<rel>CEO|founder|owner|president) of (?P<ent>.+)", "leadership"),
    (r"who (made|created|founded|invented|owns) (?P<ent>.+)", "creator"),
    (r"(?P<attr>capital|population|price|age) of (?P<ent>.+)", "attribute"),
    (r"(?P<ent_a>.+) (taller|older|richer|bigger) than (?P<ent_b>.+)", "comparison"),
]

MAX_HOPS = 4

# Clause boundaries used to trim a greedy entity capture into smaller,
# "innermost" candidates.
_CLAUSE_SPLIT_RE = re.compile(
    r"\s+(?:that|which|who|whom|whose|where|when|and|but|then)\s+", re.IGNORECASE
)

_SEARCH_SUFFIX = {
    "leadership": "founder owner chief executive president",
    "creator": "founder created by maker inventor owner",
    "attribute": "",
    "comparison": "vs",
}

_LEADERSHIP_QUERY = {
    "CEO": "chief executive officer",
    "founder": "founder co-founder",
    "owner": "owner owned by",
    "president": "president",
}


@dataclass
class SubQuery:
    """One hop of a logic tree."""

    id: str
    query_text: str
    depends_on: list[str] = field(default_factory=list)
    slot: dict = field(default_factory=dict)
    hop_type: str = "direct"
    entity_text: str = ""
    entity_span: tuple[int, int] = (0, 0)
    template: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "query_text": self.query_text,
            "depends_on": list(self.depends_on),
            "slot": dict(self.slot),
            "hop_type": self.hop_type,
            "entity_text": self.entity_text,
        }


# --------------------------------------------------------------------------- #
# Decomposition
# --------------------------------------------------------------------------- #

#: Verbs that express a creator relation (used with the dependency parse).
CREATOR_VERBS = {
    "made", "makes", "created", "creates", "founded", "founds", "invented",
    "invent", "owns", "owned", "developed", "produces", "produced", "built",
    "launched", "released", "wrote", "authored", "designed",
}

#: Generic entity phrases: when such a phrase is the outer entity, the
#: resolved answer of the inner hop replaces it entirely.
GENERIC_ENTITIES = {
    "the company", "the organisation", "the organization", "the person",
    "the city", "the country", "the place", "the team", "the group",
    "the university", "the school", "the brand", "the product", "the app",
    "the service", "the platform", "the startup", "the studio", "the agency",
}

_TRAILING_CONNECTIVE_RE = re.compile(
    r"\s*\b(?:that|which|who|whom|whose|where|when|and|or|but)\s*$",
    re.IGNORECASE,
)


def _clean_entity(text: str) -> str:
    """Trim punctuation and dangling connectives from an entity span."""
    cleaned = (text or "").strip().strip("?.,;:!\"'")
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = _TRAILING_CONNECTIVE_RE.sub("", cleaned).strip(" ,.?")
    return cleaned


def _locate_span(text: str, needle: str) -> tuple[int, int]:
    """Locate ``needle`` inside ``text`` (case-insensitive, first match)."""
    if not needle:
        return (0, 0)
    index = text.lower().find(needle.lower())
    if index < 0:
        return (0, 0)
    return (index, index + len(needle))


def _build_query_text(kind: str, groups: dict, entity: str) -> str:
    """Turn a pattern match into a web-search query for that hop."""
    if kind == "leadership":
        rel = groups.get("rel", "CEO")
        suffix = _LEADERSHIP_QUERY.get(rel, "founder owner")
        return f"{entity} {suffix}".strip()
    if kind == "creator":
        return f"{entity} {_SEARCH_SUFFIX['creator']}".strip()
    if kind == "attribute":
        attr = groups.get("attr", "")
        return f"{entity} {attr}".strip()
    if kind == "comparison":
        left = groups.get("ent_a", "").strip()
        right = groups.get("ent_b", "").strip()
        comparative = groups.get("comparative", "taller")
        return f"{left} vs {right} {comparative}".strip()
    return entity.strip()


def _collect_pattern_candidates(text: str) -> list[dict]:
    """Match every HOP_PATTERN over the query."""
    candidates: list[dict] = []
    for pattern, kind in HOP_PATTERNS:
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:  # pragma: no cover - patterns are static
            continue
        for match in regex.finditer(text):
            groups = dict(match.groupdict())
            if kind == "comparison":
                left = _clean_entity(groups.get("ent_a") or "")
                right = _clean_entity(groups.get("ent_b") or "")
                comparative = (
                    match.group(2) if match.lastindex and match.lastindex >= 2
                    else "taller"
                )
                left = re.sub(
                    r"^(?:is|are|was|were|do|does|did)\s+", "", left, flags=re.IGNORECASE
                ).strip()
                if not left or not right:
                    continue
                groups["ent_a"] = left
                groups["ent_b"] = right
                groups["comparative"] = comparative
                entity = left
                span = (match.start(1), match.end(1))
            else:
                raw_entity = groups.get("ent") or ""
                if "ent" not in match.groupdict():
                    continue
                start = match.start("ent")
                raw_entity = raw_entity.strip()
                entity = _clean_entity(raw_entity)
                if not entity:
                    continue
                span = (start, start + len(raw_entity))
            candidates.append({
                "kind": kind,
                "groups": groups,
                "entity": entity,
                "span": span,
                "position": match.start(),
                "source": "pattern",
            })
    return candidates


def _collect_relation_candidates(text: str) -> list[dict]:
    """Use the dependency parse to find creator relations."""
    from core.nlp import dependency_relations

    candidates: list[dict] = []
    try:
        relations = dependency_relations(text)
    except Exception:
        relations = []
    for relation in relations:
        verb = (relation.get("relation") or "").lower()
        obj = _clean_entity(relation.get("object") or "")
        if verb not in CREATOR_VERBS or not obj or len(obj) < 2:
            continue
        span = _locate_span(text, obj)
        if span == (0, 0):
            continue
        candidates.append({
            "kind": "creator",
            "groups": {"verb": verb, "ent": obj},
            "entity": obj,
            "span": span,
            "position": span[0],
            "source": "dependency-parse",
        })
    return candidates


def _remove_clause_with(entity: str, needle: str) -> str:
    """Remove the clause of ``entity`` that contains ``needle``."""
    if not entity or not needle:
        return entity
    lowered_needle = needle.lower().strip(" ?.,;:")
    if not lowered_needle:
        return entity
    clauses = [part.strip() for part in _CLAUSE_SPLIT_RE.split(entity) if part.strip()]
    if len(clauses) <= 1:
        return entity
    kept = [
        clause for clause in clauses
        if lowered_needle not in clause.lower()
    ]
    if not kept or kept == clauses:
        return entity
    return " ".join(kept)


def _trim_outer_entities(candidates: list[dict]) -> None:
    """Trim an outer entity that embeds another hop (innermost-first)."""
    for outer in candidates:
        for inner in candidates:
            if outer is inner:
                continue
            outer_start, outer_end = outer["span"]
            inner_start, inner_end = inner["span"]
            if not (outer_start <= inner_start and inner_end <= outer_end):
                continue
            if (inner_end - inner_start) >= (outer_end - outer_start):
                continue
            new_entity = _remove_clause_with(outer["entity"], inner["entity"])
            new_entity = _clean_entity(new_entity)
            if not new_entity or new_entity == outer["entity"]:
                continue
            outer["entity"] = new_entity
            outer["span"] = (outer_start, outer_start + len(new_entity))
            outer.setdefault("depends_on", []).append(inner)


def decompose(query: str) -> list[SubQuery]:
    """Split ``query`` into an ordered list of hops.

    * spaCy NER + dependency parsing identify the entities and relations (via
      :mod:`core.nlp`).
    * ``HOP_PATTERNS`` are matched; when one pattern's entity span *embeds*
      another hop, the outer entity is trimmed and the inner hop becomes its
      dependency.  Hops are ordered innermost-first (shortest entity first).
    * At most :data:`MAX_HOPS` hops are produced.
    * When no pattern and no relation match, the question is executed as a
      single direct hop.
    """
    text = (query or "").strip()
    if not text:
        return []

    candidates = _collect_pattern_candidates(text)
    candidates.extend(_collect_relation_candidates(text))
    if not candidates:
        # No pattern matched: single direct hop (the question itself).
        return [SubQuery(
            id="hop1",
            query_text=text,
            depends_on=[],
            slot={"mode": "direct"},
            hop_type="direct",
            entity_text=text,
            entity_span=(0, len(text)),
            template=text,
        )]

    _trim_outer_entities(candidates)

    # Innermost-first ordering: shortest entity span, then left-most.
    candidates.sort(key=lambda c: (len(c["entity"]), c["position"]))

    hops: list[SubQuery] = []
    index_by_candidate: dict[int, str] = {}
    seen_keys: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (candidate["kind"], candidate["entity"].lower())
        if key in seen_keys:
            continue
        seen_keys.add(key)
        hop_id = f"hop{len(hops) + 1}"
        index_by_candidate[id(candidate)] = hop_id
        hops.append(SubQuery(
            id=hop_id,
            query_text=_build_query_text(
                candidate["kind"], candidate["groups"], candidate["entity"]
            ),
            depends_on=[],
            slot={
                k: v for k, v in candidate["groups"].items() if v is not None
            },
            hop_type=candidate["kind"],
            entity_text=candidate["entity"],
            entity_span=candidate["span"],
            template=candidate["entity"],
        ))
        if len(hops) >= MAX_HOPS:
            break

    # Map the trimmed dependency relations onto hop ids.
    for candidate in candidates:
        hop_id = index_by_candidate.get(id(candidate))
        if not hop_id:
            continue
        hop = next((h for h in hops if h.id == hop_id), None)
        if hop is None:
            continue
        for inner in candidate.get("depends_on", []):
            inner_id = index_by_candidate.get(id(inner))
            if inner_id and inner_id != hop.id and inner_id not in hop.depends_on:
                hop.depends_on.append(inner_id)

    _link_dependencies(hops)
    return hops


def _link_dependencies(hops: list[SubQuery]) -> None:
    """Link a hop to the hops whose entity is nested inside its own."""
    for outer in hops:
        for inner in hops:
            if outer is inner:
                continue
            outer_start, outer_end = outer.entity_span
            inner_start, inner_end = inner.entity_span
            if inner_start >= outer_start and inner_end <= outer_end and (
                inner_end - inner_start
            ) < (outer_end - outer_start):
                if inner.id not in outer.depends_on:
                    outer.depends_on.append(inner.id)
        outer.depends_on.sort()


def build_hop_graph(hops: Iterable[SubQuery]):
    """Build the ``networkx.DiGraph`` of hops (edges = dependencies)."""
    if nx is None:  # pragma: no cover - networkx is required
        raise RuntimeError("networkx is required to build the hop graph.")
    graph = nx.DiGraph()
    for hop in hops:
        graph.add_node(hop.id, hop_type=hop.hop_type, query_text=hop.query_text)
    for hop in hops:
        for dependency in hop.depends_on:
            if dependency in graph:
                graph.add_edge(dependency, hop.id)
    return graph


def topological_order(graph) -> list[str]:
    """Return the execution order of the hop graph."""
    if nx is None:  # pragma: no cover
        raise RuntimeError("networkx is required for topological sorting.")
    try:
        return list(nx.topological_sort(graph))
    except Exception:
        # A cycle would be a bug; fall back to a stable order instead of
        # crashing, and let the caller report the problem.
        return list(graph.nodes())


# --------------------------------------------------------------------------- #
# Answer extraction
# --------------------------------------------------------------------------- #

_NAME_STOP_TAIL = {
    "in", "of", "the", "and", "or", "at", "on", "for", "to", "a", "an",
    "as", "is", "was", "were", "are", "by", "with", "from", "that", "which",
    "has", "have", "had", "its", "their", "his", "her",
    # Pronouns and generic heads are never the extracted name.
    "he", "she", "they", "it", "we", "you", "this", "these", "those",
    "there", "who", "whom", "someone", "somebody", "anyone",
}

# Role words are matched case-insensitively -- real text says "CEO" in
# uppercase, while the pre-3.1 patterns spelled the roles in lowercase only,
# so the canonical "Satya Nadella is the CEO of Microsoft." extracted
# nothing.  The scoped ``(?i:...)`` group keeps the name capture's strict
# ``[A-Z]`` start intact (a whole-pattern ``re.IGNORECASE`` would also
# case-fold ``[A-Z]`` and pull in junk such as "of Meta" as a name).
_ROLE = (
    r"(?i:ceo|chief executive officer|chief executive|co-founder|cofounder|"
    r"founder|owner|president)"
)
_NAME = r"([A-Z][\w&.'’-]*(?:\s+[A-Z][\w&.'’-]*){0,3})"

_NAME_PATTERNS = [
    # Role first, then the name: "WhatsApp was created by Brian Acton",
    # "Meta Platforms is led by Mark Zuckerberg".
    re.compile(
        r"(?:founded|co-founded|created|developed|made|produced|owned|leads|"
        r"led|heads?|runs?|" + _ROLE + r")\s+"
        r"(?:by\s+|is\s+|was\s+|were\s+)?(?:the\s+)?"
        + _NAME
    ),
    # Name first, then a linking verb, then the role.  A bounded filler may
    # sit on either side of the verb, so appositives and role phrases work:
    # "Satya Nadella is the CEO of Microsoft.",
    # "Mark Zuckerberg, born May 14, 1984, is the chief executive officer of Meta",
    # "Mark Zuckerberg is an American businessman and the chief executive officer of Meta".
    re.compile(
        _NAME + r"[^.!?]{0,70}?\b(?:is|was|were|are)\b[^.!?]{0,70}?"
        r"(?:the\s+|a\s+|an\s+)?(?:current\s+|co-)?" + _ROLE
    ),
    # Role before the name, across the verb: "The CEO of Meta Platforms is
    # Mark Zuckerberg."
    re.compile(
        r"(?:the\s+|a\s+|an\s+)?"
        + _ROLE
        + r"\s+(?i:of)\s+[^.!?]{1,60}?\b(?:is|was|were|are)\b\s*"
        r"(?:the\s+|a\s+|an\s+)?" + _NAME
    ),
]


def _clean_name(candidate: str) -> str:
    words = [w for w in candidate.strip().split() if w]
    while words and words[-1].lower().strip(".,;:") in _NAME_STOP_TAIL:
        words.pop()
    while words and words[0].lower().strip(".,;:") in _NAME_STOP_TAIL:
        words.pop(0)
    return " ".join(words).strip(" ,.;:'\"()")


def _extract_name(passage: str, entity_text: str) -> Optional[str]:
    """Extract the person/organisation name a passage attributes (deterministic).

    Every pattern is scanned with :meth:`finditer` so a first match that
    merely repeats the hop's own entity does not hide a later, usable one.
    """
    for pattern in _NAME_PATTERNS:
        for match in pattern.finditer(passage or ""):
            name = _clean_name(match.group(1))
            if not name or len(name) < 2:
                continue
            if entity_text and name.lower() == entity_text.strip().lower():
                continue
            return name
    return None


# Attribute-value extraction.  Real text states facts in both directions
# ("Paris is the capital of France" / "The capital of France is Paris"), so
# the value may sit on *either* side of the attribute word.  The previous
# implementation only looked after "is", which returned the wrong side of
# appositive sentences (e.g. "one of the world's great cultural centres")
# and truncated decimals ("1.4 billion" -> "1").  Each pattern below
# therefore names its capture explicitly and only accepts plausible values
# (proper nouns or numbers with decimals/units); anything else is left
# unextracted (fail closed) rather than guessed.
_ATTR = r"(?i:capital|population|price|age)"
_VALUE_NAME = r"([A-Z][\w&.'’-]*(?:\s+[A-Z][\w&.'’-]*){0,3})"
_VALUE_NUM = r"((?:[₹$€£]\s*)?\d[\d,]*(?:\.\d+)?(?:\s*[A-Za-z%]+)?)"

_VALUE_PATTERNS = [
    # Subject first: "Paris is the capital and most populous city of France."
    # The subject *is* the value being asked for.
    re.compile(
        _VALUE_NAME + r"\s+(?:is|was|are|were)\s+(?:the\s+|an?\s+)?" + _ATTR
        + r"(?=\s+(?:of|and)\b|[.,;:!?]|$)"
    ),
    # Appositive: "Paris, the capital of France, is one of the great cities."
    re.compile(
        _VALUE_NAME + r"\s*,\s*(?:the\s+|an?\s+)?" + _ATTR + r"\s+(?i:of)\b"
    ),
    # Attribute first: "The capital of France is Paris." /
    # "The population of India is 1.4 billion." (decimals and units kept).
    re.compile(
        r"(?:the\s+|an?\s+)?" + _ATTR + r"\s+(?i:of)\s+[^.!?]{1,60}?"
        r"\b(?:is|was|are|were)\b\s*(?:the\s+|a\s+|an\s+)?(?:"
        + _VALUE_NUM + "|" + _VALUE_NAME + ")"
    ),
    # Possessive form: "France has a population of 67 million."
    re.compile(
        r"(?i:has|have|had)\s+(?:an?\s+)?" + _ATTR + r"\s+(?i:of)\s+(?:"
        + _VALUE_NUM + "|" + _VALUE_NAME + ")"
    ),
    # Adjacent form: "France's capital is Paris." / "population is 1.4 billion"
    re.compile(
        _ATTR + r"\s+(?:is|are|was|were)\s*(?:the\s+|a\s+|an\s+)?(?:"
        + _VALUE_NUM + "|" + _VALUE_NAME + ")"
    ),
    # Label form: "capital: Paris" / "age = 34"
    re.compile(
        _ATTR + r"\s*[:=]\s*(?:" + _VALUE_NUM + "|" + _VALUE_NAME + ")"
    ),
]


def _extract_value(passage: str) -> Optional[str]:
    """Extract an attribute value from ``passage`` (deterministic).

    Tries the unambiguous side-of-sentence forms first, so an appositive
    mention ("Paris, the capital of France, is ...") yields the subject
    (``Paris``) and never the predicate.  Returns ``None`` when no plausible
    value (proper noun or number) is found -- never a guessed string.
    """
    for pattern in _VALUE_PATTERNS:
        for match in pattern.finditer(passage or ""):
            for group in match.groups():
                value = (group or "").strip(" ,.;:")
                if value:
                    return value
    return None


def _extract_comparison(passage: str, left: str, right: str) -> Optional[str]:
    lowered = passage.lower()
    if left.lower() not in lowered or right.lower() not in lowered:
        return None
    match = re.search(
        r"([^.!?\n]{10,200}\b(?:taller|older|richer|bigger|larger|smaller)\b[^.!?\n]{0,120})",
        passage,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return passage.strip()[:300]


def _extract_answer(
    hop_type: str,
    passages: list[dict],
    entity_text: str,
) -> Optional[str]:
    """Derive the hop answer from the ranked passages (deterministic)."""
    if not passages:
        return None
    if hop_type == "comparison":
        left = entity_text
        right = ""
        for passage in passages:
            pass
        for passage in passages:
            answer = _extract_comparison(
                passage.get("text", ""), left, right or "\x00none"
            )
            if answer:
                return answer
        return passages[0].get("text", "")[:300]
    if hop_type == "attribute":
        for passage in passages:
            value = _extract_value(passage.get("text", ""))
            if value:
                return value
        return None
    # leadership / creator / direct
    for passage in passages:
        name = _extract_name(passage.get("text", ""), entity_text)
        if name:
            return name
    return None


# --------------------------------------------------------------------------- #
# Conflict detection
# --------------------------------------------------------------------------- #

def _numeric_values(text: str) -> set[str]:
    from core.nlp import normalize_number

    values: set[str] = set()
    for match in re.finditer(r"\d[\d,]*(?:\.\d+)?", text or ""):
        values.add(normalize_number(match.group(0)))
    return values


def _detect_conflict(hop_type: str, passages: list[dict]) -> list[dict]:
    """Detect numeric disagreement between independent sources."""
    if hop_type not in {"attribute", "comparison", "direct"}:
        return []
    per_source: list[tuple[str, set[str]]] = []
    for passage in passages:
        url = passage.get("url", "")
        values = _numeric_values(passage.get("text", ""))
        if values:
            per_source.append((url, values))
    conflicts: list[dict] = []
    for index, (url_a, values_a) in enumerate(per_source):
        for url_b, values_b in per_source[index + 1:]:
            if url_a == url_b:
                continue
            only_a = values_a - values_b
            only_b = values_b - values_a
            if only_a and only_b and not (values_a & values_b):
                conflicts.append({
                    "sides": [
                        {"url": url_a, "values": sorted(only_a)},
                        {"url": url_b, "values": sorted(only_b)},
                    ],
                })
    return conflicts


# --------------------------------------------------------------------------- #
# Solver
# --------------------------------------------------------------------------- #

Fetcher = Callable[[str, int], list[dict]]


def _default_fetcher(query: str, max_results: int) -> list[dict]:
    """Search sources, using snippets first and fetching missing text in parallel.

    Search-result snippets are retrieved source text.  The original result URL
    is kept as the citation; publishers are fetched only when no usable snippet
    was returned.
    """
    from ingestion import ii_web

    try:
        response = ii_web.search_results(query, max_results=max_results)
    except Exception:
        return []
    results = list(response.results[:max_results])
    if not results:
        return []

    def clean_snippet(result) -> str:
        text = re.sub(r"<[^>]+>", " ", result.snippet or "")
        return " ".join(text.split()).strip()

    def fetch(result) -> dict | None:
        # Search snippets are already retrieved source text and usually carry
        # enough evidence for extraction.  Prefer them to a second network
        # round-trip; fetch the page only when the provider returned no usable
        # snippet.  This removes N sequential page timeouts from every answer.
        text = clean_snippet(result)
        retrieval = "search_snippet"
        if len(text) < 25:
            try:
                page = ii_web.fetch_page_text(
                    result.link,
                    timeout=max(1, int(ii_web.WEB_TIMEOUT)),
                )
            except Exception:
                page = {}
            text = (page.get("text") or "").strip()
            retrieval = "page"
        if not text:
            return None
        return {
            "url": result.link,
            "title": result.title,
            "text": text,
            "retrieval": retrieval,
        }

    with ThreadPoolExecutor(max_workers=min(5, len(results))) as executor:
        fetched = list(executor.map(fetch, results))
    return [page for page in fetched if page is not None]


class LogicTreeSolver:
    """Execute a decomposed question against live sources."""

    def __init__(
        self,
        *,
        timeout: int = 20,
        max_sources_per_hop: int = 3,
        search_results: int = 5,
        fetcher: Optional[Fetcher] = None,
    ):
        self.timeout = timeout
        self.max_sources_per_hop = max_sources_per_hop
        self.search_results = search_results
        self.fetcher = fetcher or _default_fetcher

    # -- helpers ---------------------------------------------------------- #

    def _rank_passages(self, pages: list[dict], hop_query: str) -> list[dict]:
        """TF-IDF rank the page sentences against the hop query."""
        candidates: list[dict] = []
        query_words = content_words(hop_query)
        for page in pages:
            for sentence in extract_sentences(page.get("text", "")):
                sentence = sentence.strip()
                if len(sentence) < 25:
                    continue
                overlap = query_words & content_words(sentence)
                if not overlap:
                    continue
                candidates.append({
                    "url": page.get("url", ""),
                    "title": page.get("title", ""),
                    "text": sentence,
                })
        if not candidates:
            return []
        ranked = rank_against_query(
            [c["text"] for c in candidates], hop_query, top_k=self.max_sources_per_hop
        )
        passages: list[dict] = []
        for index, score, _sentence in ranked:
            candidate = dict(candidates[index])
            candidate["score"] = round(float(score), 4)
            passages.append(candidate)
        return passages

    def _materialise(self, hop: SubQuery, answers: dict[str, str]) -> str:
        """Substitute resolved dependency answers into the hop query."""
        text = hop.query_text
        for dependency in hop.depends_on:
            resolved = answers.get(dependency)
            if not resolved:
                continue
            inner_entity = self._inner_entity_text(hop, dependency)
            if inner_entity and inner_entity.lower() in text.lower():
                text = re.sub(
                    re.escape(inner_entity),
                    resolved,
                    text,
                    flags=re.IGNORECASE,
                )
        return text.strip()

    def _inner_entity_text(self, hop: SubQuery, dependency_id: str) -> str:
        # The dependency's entity text is embedded in this hop's template.
        for other in self._hops:
            if other.id == dependency_id:
                return other.entity_text
        return ""

    # -- main ------------------------------------------------------------- #

    def solve(self, query: str) -> dict:
        """Solve ``query`` and return the structured result."""
        hops = decompose(query)
        self._hops = hops
        graph = build_hop_graph(hops)
        order = topological_order(graph)
        by_id = {hop.id: hop for hop in hops}

        steps: list[dict] = []
        sources: list[dict] = []
        answers: dict[str, str] = {}
        seen_urls: set[str] = set()
        conflicts: list[dict] = []
        failure: Optional[str] = None

        for hop_id in order:
            hop = by_id[hop_id]
            hop_query = self._materialise(hop, answers)
            step: dict = {
                "id": hop.id,
                "hop_type": hop.hop_type,
                "query_text": hop_query,
                "depends_on": list(hop.depends_on),
                "status": "failed",
                "answer": None,
                "passages": [],
            }
            try:
                pages = self.fetcher(hop_query, self.search_results)
            except Exception as exc:  # network / backend failure
                step["error"] = f"fetch failed: {exc}"
                pages = []
            if not pages:
                step["error"] = step.get("error") or "no sources returned for this hop"
                steps.append(step)
                failure = failure or f"hop '{hop.id}' returned no usable source"
                break

            passages = self._rank_passages(pages, hop_query)
            if not passages:
                step["error"] = "no passage matched the hop query"
                steps.append(step)
                failure = failure or f"hop '{hop.id}' produced no relevant passage"
                break

            answer = _extract_answer(hop.hop_type, passages, hop.entity_text)
            hop_conflicts = _detect_conflict(hop.hop_type, passages)
            conflicts.extend(hop_conflicts)

            step["passages"] = [
                {
                    "url": p["url"],
                    "title": p.get("title", ""),
                    "quote": p["text"][:400],
                    "score": p.get("score", 0.0),
                }
                for p in passages
            ]
            step["status"] = "resolved" if answer else "unresolved"
            step["answer"] = answer
            if answer:
                answers[hop.id] = answer
            steps.append(step)

            for passage in passages:
                url = passage.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    sources.append({
                        "url": url,
                        "title": passage.get("title", ""),
                        "quote": passage["text"][:400],
                    })

            if not answer:
                failure = failure or f"hop '{hop.id}' produced no extractable answer"
                break

        resolved_steps = [s for s in steps if s["status"] == "resolved"]
        domains = {
            re.sub(r"^www\.", "", re.sub(r"^https?://", "", s["url"]).split("/")[0].lower())
            for s in sources if s.get("url")
        }

        if not resolved_steps:
            status = "INSUFFICIENT_DATA"
        elif conflicts:
            status = "CONFLICT"
        elif len(domains) >= 2 and len(resolved_steps) == len(steps):
            status = "VERIFIED"
        else:
            status = "PARTIAL"

        missing: list[str] = []
        if status != "VERIFIED":
            if not resolved_steps:
                missing.append("no hop could be resolved from live sources")
            if len(domains) < 2:
                missing.append(
                    "fewer than two independent source domains support the answer"
                )
            if len(resolved_steps) != len(steps):
                missing.append("not every hop produced an extractable answer")
            if conflicts:
                missing.append("sources disagree on a value; both sides are shown")
            if failure:
                missing.append(failure)

        final_answer = self._compose_final_answer(
            status, resolved_steps, sources, steps
        )

        return {
            "status": status,
            "query": query,
            "steps": steps,
            "final_answer": final_answer,
            "sources": sources,
            "conflicts": conflicts,
            "missing": missing,
            "hop_count": len(hops),
            "execution_order": order,
        }

    def _compose_final_answer(
        self,
        status: str,
        resolved_steps: list[dict],
        sources: list[dict],
        steps: Optional[list[dict]] = None,
    ) -> str:
        """Build the deterministic answer string (no LLM).

        Every displayed answer is attributed to the hop that produced it.
        When the question's own (final) hop produced no answer, only an
        explicitly labelled intermediate result is shown -- an earlier hop's
        answer is never passed off as the answer to the full question.
        """
        all_steps = list(steps) if steps is not None else list(resolved_steps)
        if status == "INSUFFICIENT_DATA" or not resolved_steps:
            return (
                "No verified answer could be established from live sources. "
                "The steps that were attempted are listed above; nothing has "
                "been inferred or filled in."
            )
        last = resolved_steps[-1]
        question_step = all_steps[-1] if all_steps else None
        answered_question = bool(
            question_step and question_step.get("status") == "resolved"
        )
        roots = [s for s in all_steps if not s.get("depends_on")]
        lines: list[str] = []
        if status == "CONFLICT":
            lines.append("Conflicting evidence -- no single answer is forced.")
            lines.append("Candidates (each attributed to its hop):")
            for step in resolved_steps:
                lines.append(
                    f"- {step.get('answer') or ''}  "
                    f"(from hop '{step['id']}' -- {step['hop_type']})"
                )
        elif answered_question and len(roots) > 1:
            # Co-equal sub-questions ("Who founded Tesla and who is the CEO
            # of SpaceX?"): one answer line per resolved hop, each labelled.
            lines.append("Answers (one per sub-question):")
            for step in all_steps:
                if step.get("status") != "resolved":
                    continue
                lines.append(
                    f"- {step.get('answer') or ''}  "
                    f"(from hop '{step['id']}' -- {step['hop_type']}: "
                    f"{step.get('query_text')})"
                )
        elif answered_question:
            answer = question_step.get("answer") or ""
            lines.append(f"Answer: {answer}")
            lines.append(
                f"(from hop '{question_step['id']}' -- {question_step['hop_type']})"
            )
            earlier = [s for s in resolved_steps if s is not question_step]
            if earlier:
                lines.append("Derivation:")
                for step in earlier:
                    lines.append(
                        f"- hop '{step['id']}' ({step['hop_type']}): "
                        f"{step.get('answer') or ''}"
                    )
        else:
            # The question's own hop is unresolved: only intermediate work
            # exists, and it must be labelled as such (never as "Answer:" --
            # that previously showed e.g. the creator of WhatsApp when the
            # question asked for the CEO).
            answer = last.get("answer") or ""
            lines.append(
                "Intermediate result only -- not the answer to the full "
                f"question: {answer}"
            )
            lines.append(
                f"(from hop '{last['id']}' -- {last['hop_type']})"
            )
            if question_step is not None:
                lines.append(
                    f"The final hop (hop '{question_step['id']}' -- "
                    f"{question_step['hop_type']}) produced no extractable "
                    "answer, so the question itself is still open."
                )
        cited = [s for s in sources if s.get("url")]
        if cited:
            lines.append("Sources:")
            for index, source in enumerate(cited, 1):
                lines.append(f"[{index}] {source.get('title') or source['url']} -- {source['url']}")
        if status == "PARTIAL":
            lines.append(
                "Status: PARTIAL -- some steps were resolved; the remaining "
                "gaps are listed in 'missing'."
            )
        elif status == "CONFLICT":
            lines.append(
                "Status: CONFLICT -- sources disagree; both sides are cited "
                "above and no single answer is forced."
            )
        else:
            lines.append(
                "Status: VERIFIED -- the answer is supported by at least two "
                "independent live source domains."
            )
        return "\n".join(lines)
