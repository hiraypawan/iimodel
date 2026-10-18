"""Interviewer -- explicit slot extraction and (at most two) clarifying questions.

The interviewer is *rule based on purpose*: the slots it looks for are written
out as explicit regular expressions so that the behaviour can be inspected and
tested.  There is no hand-waved "decision tree" and no LLM involvement.

Contract
--------
* Extraction returns a :class:`ConstraintTree` -- a plain ``dict`` that the
  DAG planner and the critic loop both consume.
* At most :data:`Interviewer.MAX_QUESTIONS` questions are ever asked.
* When the user does not answer, the pipeline **proceeds with documented
  defaults** -- the interviewer never blocks the request on trivia.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from core.nlp import extract_entities

__all__ = [
    "Interviewer",
    "ConstraintTree",
    "SLOT_PATTERNS",
    "SLOT_QUESTIONS",
    "SLOT_DEFAULTS",
]


@dataclass
class Constraint:
    """One extracted slot value."""

    slot: str
    value: str
    source: str = "pattern"
    confidence: float = 0.6

    def to_dict(self) -> dict:
        return {
            "slot": self.slot,
            "value": self.value,
            "source": self.source,
            "confidence": round(float(self.confidence), 3),
        }


#: Explicit slot patterns.  Every group must be a non-empty string.
SLOT_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "date": [
        ("iso", re.compile(r"\b\d{4}-\d{2}-\d{2}\b")),
        ("day_first", re.compile(
            r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?"
            r"(?:January|February|March|April|May|June|July|August|September|"
            r"October|November|December)(?:\s+\d{4})?\b"
        )),
        ("month_year", re.compile(
            r"\b(?:January|February|March|April|May|June|July|August|September|"
            r"October|November|December)\s+\d{4}\b"
        )),
        ("quarter", re.compile(r"\bQ[1-4]\s*(?:of\s*)?\d{4}\b", re.IGNORECASE)),
        ("year", re.compile(r"\b(?:19|20)\d{2}\b")),
        ("relative", re.compile(
            r"\b(?:today|tomorrow|yesterday|next week|next month|this week|"
            r"this month|this quarter|this year|by end of (?:the )?(?:week|month|quarter|year))\b",
            re.IGNORECASE,
        )),
    ],
    "money": [
        ("symbol", re.compile(
            r"(?:₹|Rs\.?|INR|USD|EUR|GBP|\$|€|£)\s?\d[\d,]*(?:\.\d+)?"
            r"(?:\s?(?:k|m|b|lakh|crore|thousand|million|billion))?",
            re.IGNORECASE,
        )),
        ("words", re.compile(
            r"\b(?:under|below|above|over|around|upto|up to|within|budget of|"
            r"budget|price of|cost of|worth)\s+[₹$€£]?\s?[\d,.]+\s*"
            r"(?:k|m|b|lakh|crore|thousand|million|billion)?\b",
            re.IGNORECASE,
        )),
    ],
    "audience": [
        ("for_clause", re.compile(
            r"\bfor\s+(?:the\s+)?([A-Za-z][A-Za-z0-9 ,\-]{2,40}?)\s+"
            r"(?:who|that|which|audience|users?|customers?|students?|developers?)\b",
            re.IGNORECASE,
        )),
        ("audience_word", re.compile(
            r"\b(?:audience|target audience|end users?|customers?|students?|"
            r"developers?|beginners?|startups?|enterprise|teams?|kids|children|"
            r"parents|seniors|professionals?)\b",
            re.IGNORECASE,
        )),
    ],
    "platform": [
        ("named", re.compile(
            r"\b(?:on|for|targeting|target|in)\s+(?:the\s+)?"
            r"((?:Android|iOS|IOS|Web|web|"
            r"Windows|Linux|macOS|Android TV|CLI|Terminal|Discord|Slack|"
            r"Telegram|WhatsApp|Instagram|YouTube|Twitter|X)\b)",
            re.IGNORECASE,
        )),
        ("stack_word", re.compile(
            r"\b(?:web app|mobile app|desktop app|android app|ios app|"
            r"web app|cli|api|backend|frontend|website|landing page|dashboard)\b",
            re.IGNORECASE,
        )),
    ],
    "tech_stack": [
        ("named", re.compile(
            r"\b(?:using|with|in)\s+([A-Za-z0-9+#.\-]+(?:\s*,\s*[A-Za-z0-9+#.\-]+){0,5})"
            r"\s*(?:stack|framework|languages?|tech)\b",
            re.IGNORECASE,
        )),
        ("frameworks", re.compile(
            r"\b(Python|JavaScript|TypeScript|Go|Golang|Rust|Java|Kotlin|Swift|"
            r"React|Vue|Angular|Svelte|Node\.js|Django|Flask|FastAPI|"
            r"Next\.js|Express|GraphQL|PostgreSQL|MySQL|MongoDB|Redis|"
            r"Docker|Kubernetes|Terraform|AWS|Azure|GCP)\b"
        )),
    ],
}

#: The single question asked for each slot (no branching, no loops).
SLOT_QUESTIONS: dict[str, str] = {
    "date": (
        "What date or deadline should I work with "
        "(for example: 2026-04-01, Q2 2026, or 'next month')?"
    ),
    "money": (
        "What budget or price range should I assume "
        "(for example: under Rs.50,000 or $2,000)?"
    ),
    "audience": (
        "Who is the target audience "
        "(for example: first-time users, enterprise buyers, students)?"
    ),
    "platform": (
        "Which platform should this target "
        "(for example: web, Android, iOS, CLI, Discord)?"
    ),
    "tech_stack": (
        "Which tech stack should I use "
        "(for example: Python + Flask, React + Node, PostgreSQL)?"
    ),
}

#: Documented defaults used when the user does not answer.
SLOT_DEFAULTS: dict[str, str] = {
    "date": "no specific date -- use the most recent available sources",
    "money": "no budget constraint given",
    "audience": "a general, technically literate audience",
    "platform": "platform not specified -- keep the answer platform-neutral",
    "tech_stack": "no tech stack constraint given",
}

#: Slots that matter for a given task family.
_CRITICAL_BY_INTENT: dict[str, list[str]] = {
    "code": ["platform", "tech_stack"],
    "content": ["audience", "platform"],
    "research": ["date"],
    "plan": ["date", "audience"],
    "analysis": ["date", "audience"],
    "general": ["audience", "date"],
}

_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "code": ("build", "code", "implement", "script", "function", "app", "api",
             "program", "tool", "website", "endpoint", "cli"),
    "content": ("blog", "post", "article", "email", "newsletter", "caption",
                "copy", "announcement", "thread", "story"),
    "research": ("research", "compare", "review", "investigate", "find out",
                 "latest", "current", "who is", "what is"),
    "plan": ("plan", "roadmap", "launch", "schedule", "timeline", "strategy",
             "rollout", "milestone"),
    "analysis": ("analyse", "analyze", "compare", "evaluate", "impact",
                 "trend", "breakdown"),
}

_STEP_MARKERS = (
    " and then ", " then ", " after that ", " followed by ", " first ",
    " secondly ", " thirdly ", " finally ", " next ", " step 1", " step 2",
    " also ", " plus ", " additionally ",
)

_STEP_VERBS = (
    "create", "build", "write", "design", "deploy", "test", "measure",
    "collect", "analyse", "analyze", "publish", "launch", "document",
    "research", "generate", "prepare", "review", "plan", "implement",
    "extract", "convert", "compare", "summarise", "summarize",
)


@dataclass
class ConstraintTree:
    """Structured constraints extracted from a task."""

    task: str = ""
    is_multi_step: bool = False
    slots: dict[str, list[dict]] = field(default_factory=dict)
    filled: list[str] = field(default_factory=list)
    missing_critical: list[str] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    defaults: dict[str, str] = field(default_factory=dict)
    answers: dict[str, str] = field(default_factory=dict)
    task_family: str = "general"

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "task_family": self.task_family,
            "is_multi_step": self.is_multi_step,
            "slots": {k: list(v) for k, v in self.slots.items()},
            "filled_slots": list(self.filled),
            "missing_critical": list(self.missing_critical),
            "questions": list(self.questions),
            "defaults": dict(self.defaults),
            "answers": dict(self.answers),
        }

    def checklist(self) -> list[dict]:
        """Constraint checklist consumed by the critic loop."""
        items: list[dict] = []
        for slot, values in self.slots.items():
            items.append({
                "name": f"constraint:{slot}",
                "status": "pass" if values else "info",
                "detail": (
                    f"{slot} = {values[0]['value']}" if values
                    else f"{slot} not specified; default applies"
                ),
            })
        for slot in self.missing_critical:
            items.append({
                "name": f"constraint:{slot}",
                "status": "info",
                "detail": f"{slot} was not specified; used default "
                          f"'{self.defaults.get(slot, 'unspecified')}'",
            })
        return items


class Interviewer:
    """Extract constraints and ask at most two targeted questions."""

    MAX_QUESTIONS = 2

    def __init__(self) -> None:
        self._asked_count = 0

    # -- extraction -------------------------------------------------------- #

    def extract_constraints(self, prompt: str) -> ConstraintTree:
        """Extract the slot values that are explicitly present in ``prompt``."""
        text = (prompt or "").strip()
        tree = ConstraintTree(task=text)
        tree.task_family = self._task_family(text)
        tree.is_multi_step = self._is_multi_step(text)

        slots: dict[str, list[dict]] = {}
        for slot, patterns in SLOT_PATTERNS.items():
            found: list[dict] = []
            for source, pattern in patterns:
                for match in pattern.finditer(text):
                    value = match.group(0).strip()
                    if not value:
                        continue
                    if any(item["value"].lower() == value.lower() for item in found):
                        continue
                    found.append(Constraint(
                        slot=slot,
                        value=value,
                        source="pattern",
                        confidence=0.8 if source not in {"year", "audience_word"} else 0.5,
                    ).to_dict())
            if found:
                slots[slot] = found

        # spaCy NER helps for audience/platform style slots (organisations,
        # products) that no regular expression would catch.
        for entity in extract_entities(text):
            if entity.get("label") in {"ORG", "PRODUCT", "GPE", "LOC"}:
                for slot in ("audience", "platform"):
                    if slot in slots:
                        continue
                    slots[slot] = [Constraint(
                        slot=slot,
                        value=entity["text"],
                        source="spacy-ner",
                        confidence=0.4,
                    ).to_dict()]
                    break

        tree.slots = slots
        tree.filled = sorted(slots.keys())
        tree.defaults = dict(SLOT_DEFAULTS)
        tree.answers = {
            slot: values[0]["value"] for slot, values in slots.items() if values
        }
        critical = self._critical_slots(tree.task_family)
        tree.missing_critical = [slot for slot in critical if slot not in tree.filled]
        return tree

    def _task_family(self, text: str) -> str:
        lowered = (text or "").lower()
        best_family = "general"
        best_score = 0
        for family, keywords in _INTENT_KEYWORDS.items():
            score = sum(1 for keyword in keywords if keyword in lowered)
            if score > best_score:
                best_family = family
                best_score = score
        return best_family

    def _critical_slots(self, family: str) -> list[str]:
        return list(_CRITICAL_BY_INTENT.get(family, _CRITICAL_BY_INTENT["general"]))

    def _is_multi_step(self, text: str) -> bool:
        lowered = f" {(text or '').lower()} "
        if any(marker in lowered for marker in _STEP_MARKERS):
            return True
        verbs = sum(1 for verb in _STEP_VERBS if re.search(
            rf"\b{re.escape(verb)}\b", lowered))
        sentences = [s for s in re.split(r"[.!?\n]+", text or "") if s.strip()]
        return verbs >= 2 and len(sentences) >= 2

    # -- questions --------------------------------------------------------- #

    def needs_clarification(
        self,
        prompt: str,
        context: Optional[dict] = None,
    ) -> bool:
        """True when the task is multi-step and fewer than two slots are known."""
        tree = self.extract_constraints(prompt)
        critical = self._critical_slots(tree.task_family)
        filled_critical = [slot for slot in critical if slot in tree.filled]
        if not tree.is_multi_step:
            return False
        if len(filled_critical) >= 2:
            return False
        # Context may already supply the missing information.
        if context:
            for slot in critical:
                if slot in tree.filled:
                    continue
                if context.get(slot):
                    return False
        return True

    def ask_questions(
        self,
        prompt: str,
        context: Optional[dict] = None,
    ) -> dict:
        """Return at most two targeted questions plus the default plan.

        The method is idempotent per instance: once ``MAX_QUESTIONS`` questions
        have been produced it returns an empty question list instead of
        looping.
        """
        tree = self.extract_constraints(prompt)
        critical = self._critical_slots(tree.task_family)
        missing = [slot for slot in critical if slot not in tree.filled]
        if context:
            missing = [slot for slot in missing if not context.get(slot)]
        if not tree.is_multi_step:
            # A single-step task is never questioned.
            missing = []

        remaining = max(0, self.MAX_QUESTIONS - self._asked_count)
        questions: list[dict] = []
        for slot in missing[:remaining]:
            questions.append({
                "slot": slot,
                "question": SLOT_QUESTIONS.get(slot, f"What is the {slot}?"),
                "default": SLOT_DEFAULTS.get(slot, "unspecified"),
            })
        self._asked_count += len(questions)
        tree.questions = questions
        return {
            "questions": questions,
            "constraint_tree": tree.to_dict(),
            "proceed_with_defaults": True,
            "note": (
                "At most two questions are asked; the pipeline continues with "
                "the documented defaults when they are not answered."
            ),
        }

    def build_constraint_tree(
        self,
        prompt: str,
        context: Optional[dict] = None,
    ) -> dict:
        """Return the constraint tree that the planner and critic consume."""
        tree = self.extract_constraints(prompt)
        for slot, value in (context or {}).items():
            if slot in SLOT_DEFAULTS and slot not in tree.filled and value:
                tree.slots[slot] = [Constraint(
                    slot=slot, value=str(value), source="context", confidence=0.5
                ).to_dict()]
                tree.filled = sorted(tree.slots.keys())
                tree.answers[slot] = str(value)
        tree.missing_critical = [
            slot for slot in self._critical_slots(tree.task_family)
            if slot not in tree.filled
        ]
        return tree.to_dict()
