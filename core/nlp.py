"""Shared NLP helpers for the Pure II pipeline.

Design rules
------------
* spaCy is a **lazy, optional** dependency.  The pipeline must work when the
  model (``en_core_web_sm``) is not installed, therefore every helper has a
  deterministic regex fallback.
* The fallback is *not* claimed to be equivalent to spaCy.  It is a transparent
  approximation: capitalised-sequence heuristics for names/orgs, explicit
  regexes for numbers, money, dates, and a small rule set for relations.
* Nothing in this module reasons or concludes.  It only extracts surface
  structure (entities, sentences, relations) that later stages consume.

Honesty note
------------
When the spaCy model is present the helpers use ``en_core_web_sm`` NER and the
dependency parse.  When it is absent, ``nlp_backend()`` reports ``"regex"``
so callers (and ``/api/status``) can state which one was used.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Iterable

__all__ = [
    "HAS_SPACY",
    "HAS_SPACY_MODEL",
    "nlp_backend",
    "get_nlp",
    "extract_entities",
    "extract_sentences",
    "dependency_relations",
    "content_words",
    "tokenize",
    "normalize_number",
]

try:  # pragma: no cover - import guard only
    import spacy  # type: ignore

    HAS_SPACY = True
except Exception:  # pragma: no cover - spaCy is optional
    spacy = None  # type: ignore[assignment]
    HAS_SPACY = False


@lru_cache(maxsize=1)
def _load_model():
    """Load ``en_core_web_sm`` once, or return ``None`` when unavailable."""
    if not HAS_SPACY:
        return None
    for name in ("en_core_web_sm",):
        try:
            return spacy.load(name, disable=["lemmatizer", "textcat"])
        except Exception:
            continue
    # A blank pipeline still gives us a tokenizer and sentence splitter.
    try:
        nlp = spacy.blank("en")
        nlp.add_pipe("sentencizer")
        return nlp
    except Exception:
        return None


@lru_cache(maxsize=1)
def _model_present() -> bool:
    if not HAS_SPACY:
        return False
    try:
        spacy.load("en_core_web_sm")
        return True
    except Exception:
        return False


def HAS_SPACY_MODEL() -> bool:  # noqa: N802 - factory-style predicate
    """True when the small English spaCy model is importable."""
    return _model_present()


def nlp_backend() -> str:
    """Return ``"spacy"``, ``"spacy_blank"`` or ``"regex"``."""
    if not HAS_SPACY:
        return "regex"
    model = _load_model()
    if model is None:
        return "regex"
    return "spacy" if _model_present() else "spacy_blank"


def get_nlp():
    """Return the cached spaCy pipeline, or ``None`` when spaCy is absent."""
    return _load_model()


# --------------------------------------------------------------------------- #
# Tokenisation helpers
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’._%+-]*")

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "of", "in",
    "on", "at", "to", "for", "with", "about", "into", "by", "from", "up",
    "down", "over", "under", "is", "are", "was", "were", "be", "been", "being",
    "am", "do", "does", "did", "doing", "have", "has", "had", "having", "i",
    "you", "he", "she", "it", "we", "they", "them", "his", "her", "its",
    "their", "our", "your", "my", "me", "us", "this", "that", "these",
    "those", "there", "here", "what", "which", "who", "whom", "whose",
    "when", "where", "why", "how", "all", "any", "both", "each", "few", "more",
    "most", "other", "some", "such", "no", "not", "only", "own", "same", "so",
    "than", "too", "very", "can", "will", "just", "should", "now", "as", "it",
}

# Words that frequently start a sentence and are not proper nouns.
_SENTENCE_LEADERS = {
    "the", "a", "an", "this", "that", "these", "those", "there", "it", "he",
    "she", "they", "we", "you", "i", "what", "which", "who", "when", "where",
    "why", "how", "in", "on", "at", "to", "for", "and", "or", "but", "if",
    "is", "was", "were", "are", "do", "does", "did", "has", "have", "had",
    "can", "will", "would", "should", "could", "as", "by", "from", "with",
    "after", "before", "during", "while", "since", "until", "so", "because",
}

_MONEY_RE = re.compile(
    r"(?:(?:₹|Rs\.?|INR|USD|EUR|GBP|\$|€|£)\s?\d[\d,]*(?:\.\d+)?"
    r"(?:\s?(?:billion|million|thousand|crore|lakh|b|m|k))?)"
    r"|(?:\d[\d,]*(?:\.\d+)?\s?(?:rupees|dollars|euros|pounds|billion|million|thousand))",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(
    r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"      # 1,234,567
    r"|\b\d+(?:\.\d+)?\s?%(?!\w)"            # 15 %
    r"|\b\d+(?:\.\d+)?\b"                   # 42 / 3.14
)
_DATE_ISO_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DATE_TEXT_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2}(?:,\s*\d{4})?\b"
    r"|\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)(?:\s+\d{4})?\b"
)
_YEAR_RE = re.compile(r"\b(?:1[89]\d{2}|20\d{2}|21\d{2})\b")
_PROPER_SEQUENCE_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9&'’-]+"
    r"(?:\s+(?:of|and|de|van|von|the)?\s*[A-Z][A-Za-z0-9&'’-]+)*"
)
_URL_RE = re.compile(r"https?://\S+")


def tokenize(text: str) -> list[str]:
    """Lower-cased word tokens (kept short and dependency free)."""
    return [m.group(0).lower().strip("._'-") for m in _TOKEN_RE.finditer(text or "")]


def content_words(text: str) -> set[str]:
    """Tokens that carry topical meaning (stop words removed)."""
    return {t for t in tokenize(text) if len(t) > 2 and t not in STOPWORDS}


# --------------------------------------------------------------------------- #
# Number normalisation
# --------------------------------------------------------------------------- #

_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90", "hundred": "100", "thousand": "1000",
    "million": "1000000", "billion": "1000000000",
}

_MULTIPLIERS = {
    "k": 1_000, "m": 1_000_000, "b": 1_000_000_000,
    "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000,
    "lakh": 100_000, "crore": 10_000_000,
}

_CURRENCY_WORDS = ("inr", "usd", "eur", "gbp", "rupees", "dollars", "euros", "pounds")


def normalize_number(text: str) -> str:
    """Normalise a numeric surface form so equivalent values compare equal.

    ``"15%"`` -> ``"15%"``; ``"15 percent"`` -> ``"15%"``;
    ``"1,000"`` -> ``"1000"``; ``"$1.5M"`` -> ``"1500000"``.
    """
    if text is None:
        return ""
    value = str(text).strip().lower()
    value = value.replace(",", "").replace(" ", " ")
    value = re.sub(r"\s+", " ", value).strip()

    # Currency-prefixed values: "$1.5m" -> "1500000"
    money = re.match(r"^(?:[₹$€£]|rs\.?|inr|usd|eur|gbp)\s*(\d+(?:\.\d+)?)\s*"
                     r"(k|m|b|thousand|million|billion|lakh|crore)?$", value)
    if money:
        number = float(money.group(1))
        suffix = money.group(2) or ""
        if suffix in _MULTIPLIERS:
            number *= _MULTIPLIERS[suffix]
        if number.is_integer():
            return str(int(number))
        return str(number)

    # Suffix-multiplied values: "1.5 million"
    scaled = re.match(r"^(\d+(?:\.\d+)?)\s*(k|m|b|thousand|million|billion|lakh|crore)$",
                      value)
    if scaled:
        number = float(scaled.group(1)) * _MULTIPLIERS[scaled.group(2)]
        return str(int(number)) if number.is_integer() else str(number)

    # Percentages
    percent = re.match(r"^(\d+(?:\.\d+)?)\s*(?:%|percent|pct|per\s*cent)$", value)
    if percent:
        return f"{percent.group(1)}%"

    # Plain numbers
    plain = re.match(r"^(\d+(?:\.\d+)?)$", value)
    if plain:
        number = plain.group(1)
        if number.endswith(".0"):
            number = number[:-2]
        return number

    # Number words
    if value in _NUMBER_WORDS:
        return _NUMBER_WORDS[value]

    return value


# --------------------------------------------------------------------------- #
# Entities
# --------------------------------------------------------------------------- #

def _regex_entities(text: str) -> list[dict[str, Any]]:
    """Deterministic entity approximation used when spaCy is unavailable."""
    entities: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()

    def add(start: int, end: int, label: str, surface: str) -> None:
        key = (start, end)
        if key in seen or not surface.strip():
            return
        seen.add(key)
        entities.append({
            "text": surface.strip(),
            "label": label,
            "start": start,
            "end": end,
        })

    for match in _MONEY_RE.finditer(text):
        add(match.start(), match.end(), "MONEY", match.group(0))
    for match in _DATE_ISO_RE.finditer(text):
        add(match.start(), match.end(), "DATE", match.group(0))
    for match in _DATE_TEXT_RE.finditer(text):
        add(match.start(), match.end(), "DATE", match.group(0))
    for match in _NUMBER_RE.finditer(text):
        if any(e["start"] <= match.start() < e["end"] for e in entities):
            continue
        add(match.start(), match.end(), "CARDINAL", match.group(0))
    for match in _YEAR_RE.finditer(text):
        if any(e["start"] <= match.start() < e["end"] for e in entities):
            continue
        add(match.start(), match.end(), "DATE", match.group(0))

    # Capitalised sequences (person / organisation heuristics).
    for match in _PROPER_SEQUENCE_RE.finditer(text):
        surface = match.group(0)
        tokens = surface.split()
        if not tokens:
            continue
        if len(tokens) == 1:
            token = tokens[0]
            if token.lower() in _SENTENCE_LEADERS or token.lower() in STOPWORDS:
                continue
            if len(token) < 3 or not token[0].isupper():
                continue
            if token.isupper() and len(token) <= 2:
                continue
            label = "ORG" if token.endswith(("Inc", "Ltd", "Corp", "LLC", "GmbH")) else "PERSON"
            add(match.start(), match.end(), label, surface)
            continue
        # Multi-word capitalised run: treat as ORG-ish entity.
        if tokens[0].lower() in _SENTENCE_LEADERS:
            continue
        add(match.start(), match.end(), "ORG", surface)

    entities.sort(key=lambda e: (e["start"], -len(e["text"])))
    return entities


def extract_entities(text: str) -> list[dict[str, Any]]:
    """Extract entities from ``text``.

    Returns a list of ``{"text", "label", "start", "end"}`` dictionaries.  Uses
    spaCy NER when the small model is installed; otherwise the deterministic
    regex approximation.  The two backends are *not* equivalent -- callers
    should treat the result as a candidate list, not a ground truth.
    """
    if not text or not text.strip():
        return []
    model = get_nlp()
    if model is not None and _model_present():
        try:
            doc = model(text)
            out = []
            for ent in doc.ents:
                out.append({
                    "text": ent.text,
                    "label": ent.label_,
                    "start": ent.start_char,
                    "end": ent.end_char,
                })
            if out:
                return out
        except Exception:
            pass
    return _regex_entities(text)


# --------------------------------------------------------------------------- #
# Sentences
# --------------------------------------------------------------------------- #

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\n{2,}")


def extract_sentences(text: str) -> list[str]:
    """Split ``text`` into sentences (spaCy when available, regex otherwise)."""
    if not text or not text.strip():
        return []
    model = get_nlp()
    if model is not None:
        try:
            sentences = [s.text.strip() for s in model(text).sents]
            sentences = [s for s in sentences if s]
            if sentences:
                return sentences
        except Exception:
            pass
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text) if p and p.strip()]
    return parts


# --------------------------------------------------------------------------- #
# Relations (dependency-like triples)
# --------------------------------------------------------------------------- #

_RELATION_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(
        r"^(?P<subj>.+?)\s+(?:that|which|who)\s+"
        r"(?P<verb>made|makes|created|creates|founded|founds|invented|owns|"
        r"owned|developed|produced|built|launched|released|wrote|designed)\s+"
        r"(?P<obj>.+)$",
        re.IGNORECASE,
    ), "relative_clause"),
    (re.compile(r"^(?P<subj>.+?)\s+of\s+(?P<obj>.+)$", re.IGNORECASE), "of"),
    (re.compile(
        r"^(?P<subj>.+?)\s+(?:is|was|are|were)\s+(?:the\s+)?(?P<obj>.+)$",
        re.IGNORECASE,
    ), "copula"),
    (re.compile(
        r"^(?P<subj>.+?)\s+(?:made|makes|created|creates|founded|founds|"
        r"invented|owns|owned|leads|led|wrote|published|released)\s+"
        r"(?P<obj>.+)$",
        re.IGNORECASE,
    ), "agent"),
    (re.compile(
        r"^(?P<subj>.+?)\s+(?:compared|versus|vs\.?)\s+(?P<obj>.+)$",
        re.IGNORECASE,
    ), "comparison"),
]


def dependency_relations(text: str) -> list[dict[str, str]]:
    """Return ``{"subject", "relation", "object"}`` triples.

    With the spaCy model installed the real dependency parse is used
    (``nsubj`` / ``dobj`` / ``pobj`` / ``prep``).  Without it, a small set of
    explicit surface rules is applied.  The fallback is a heuristic and is
    documented as such.
    """
    if not text or not text.strip():
        return []
    model = get_nlp()
    if model is not None and _model_present():
        try:
            doc = model(text)
            out: list[dict[str, str]] = []
            for token in doc:
                if token.dep_ in {"nsubj", "nsubjpass", "csubj"}:
                    for child in token.children:
                        if child.dep_ in {"dobj", "pobj", "attr", "oprd"}:
                            out.append({
                                "subject": token.text,
                                "relation": child.dep_,
                                "object": child.text,
                            })
                elif token.dep_ == "prep" and token.head.pos_ in {"NOUN", "PROPN"}:
                    for child in token.children:
                        if child.dep_ == "pobj":
                            out.append({
                                "subject": token.head.text,
                                "relation": f"prep:{token.lower_}",
                                "object": child.text,
                            })
                elif token.pos_ in {"VERB", "AUX"} and token.dep_ in {
                    "ROOT", "conj", "ccomp", "xcomp", "relcl", "acl", "advcl",
                }:
                    subjects = [
                        child for child in token.children
                        if child.dep_ in {"nsubj", "nsubjpass", "csubj"}
                    ]
                    objects = [
                        child for child in token.children
                        if child.dep_ in {"dobj", "pobj", "attr", "oprd", "dative"}
                    ]
                    if subjects and objects:
                        out.append({
                            "subject": subjects[0].text,
                            "relation": token.lower_,
                            "object": objects[0].text,
                        })
            if out:
                return out
        except Exception:
            pass

    out = []
    for sentence in extract_sentences(text):
        for pattern, relation in _RELATION_RULES:
            match = pattern.match(sentence.strip())
            if not match:
                continue
            subject = (match.groupdict().get("subj") or "").strip()
            obj = (match.groupdict().get("obj") or "").strip()
            verb = (match.groupdict().get("verb") or "").strip()
            if not subject or not obj:
                continue
            if relation == "relative_clause" and verb:
                out.append({
                    "subject": subject,
                    "relation": verb.lower(),
                    "object": obj,
                })
                continue
            out.append({
                "subject": subject,
                "relation": relation,
                "object": obj,
            })
    return out


def entity_spans(entities: Iterable[dict[str, Any]]) -> list[tuple[int, int]]:
    """Return ``(start, end)`` character spans for the given entities."""
    return [(int(e.get("start", 0)), int(e.get("end", 0))) for e in entities]
