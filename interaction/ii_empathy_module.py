"""Empathy module -- crisis-first support with sourced, non-clinical techniques.

Order of operations (fixed, and enforced by the code):

1. **Crisis scan runs first.**  If crisis language is detected the module
   returns crisis resources immediately and no other step runs.
2. **Sentiment bucketing** with VADER into ``frustration`` / ``anxiety`` /
   ``sadness`` buckets.
3. **Curated frameworks** -- validation plus one or two grounding techniques.
   Every technique carries a source URL.  No efficacy percentages are claimed
   anywhere: these are widely described practices, not treatments.
4. **Formatting** by the language model, which is only allowed to rephrase
   the JSON it is given.

Safety rules
------------
* The module never claims to be a therapist and never simulates feelings.
* The closing disclaimer is mandatory and is appended by code, not by the
  model, so it can never be dropped.
"""

from __future__ import annotations

import re
from typing import Any, Optional

__all__ = [
    "EmpathyModule",
    "CRISIS_RESOURCES",
    "FRAMEWORKS",
    "MANDATORY_CLOSING",
    "CRISIS_KEYWORDS",
]

MANDATORY_CLOSING = (
    "This is supportive information, not therapy. For ongoing struggles, "
    "please consider a mental-health professional."
)

#: Crisis language.  Deliberately explicit: a missed crisis phrase is far
#: more costly than a false positive, which only shows a resource list.
CRISIS_KEYWORDS: tuple[str, ...] = (
    "suicide", "suicidal", "kill myself", "killing myself", "end my life",
    "want to die", "wanna die", "dont want to live", "don't want to live",
    "do not want to live", "not want to be alive", "self harm", "self-harm",
    "cutting myself", "overdose", "no reason to live", "take my own life",
    "hang myself", "jump off", "goodbye forever", "end it all",
)

#: Crisis resources.  Region labels are informational; the list is not a
#: substitute for local emergency services.
CRISIS_RESOURCES: list[dict[str, str]] = [
    {
        "name": "Find a Helpline (international directory)",
        "contact": "https://findahelpline.com/",
        "region": "International",
    },
    {
        "name": "988 Suicide & Crisis Lifeline (call or text 988)",
        "contact": "https://www.988lifeline.org/",
        "region": "United States",
    },
    {
        "name": "iCall (TISS) helpline, 9152987821",
        "contact": "https://icallhelpline.org/",
        "region": "India",
    },
    {
        "name": "Vandrevala Foundation, 1860-2662-345",
        "contact": "https://www.vandrevalafoundation.org/",
        "region": "India",
    },
    {
        "name": "AASRA, 9820466726",
        "contact": "https://www.aasra.info/",
        "region": "India",
    },
    {
        "name": "Samaritans, 116 123",
        "contact": "https://www.samaritans.org/",
        "region": "United Kingdom / Ireland",
    },
    {
        "name": "Emergency services, 112",
        "contact": "https://www.who.int/publications/i/item/9789241550585",
        "region": "Most of Europe and many other regions",
    },
]

#: Curated frameworks.  Each framework carries a validation step and one or
#: two grounding techniques; every technique has a source URL.
FRAMEWORKS: dict[str, dict[str, Any]] = {
    "frustration": {
        "label": "Frustration / blocked progress",
        "validation": {
            "name": "Validate the effort, not the outcome",
            "summary": (
                "Name the concrete effort already spent (retries, reading "
                "docs, debugging) and confirm that repeated failure on a "
                "hard problem is a signal about the problem, not about the "
                "person."
            ),
            "source_url": "https://www.nhs.uk/mental-health/",
        },
        "techniques": [
            {
                "name": "Two-minute triage of the blocker",
                "summary": (
                    "Write the single sentence describing what is blocked, "
                    "then the single smallest test that would tell you "
                    "whether the hypothesis is wrong. Stop after the test is "
                    "written."
                ),
                "source_url": "https://www.atlassian.com/team-playbook/plays/incident-management",
            },
            {
                "name": "Paced breathing (4-6 breaths per minute for 2 minutes)",
                "summary": (
                    "Breathe in for 4 counts, out for 6, for two minutes. It "
                    "is a physiological downshift technique, not a cure."
                ),
                "source_url": "https://www.healthline.com/health/box-breathing",
            },
        ],
    },
    "anxiety": {
        "label": "Anxiety / worry",
        "validation": {
            "name": "Name the uncertainty instead of the worst case",
            "summary": (
                "Separate what is known, what is assumed, and what is "
                "unknown. Anxiety usually inflates the unknown column; "
                "writing it down shrinks it."
            ),
            "source_url": "https://www.who.int/news-room/fact-sheets/detail/mental-disorders",
        },
        "techniques": [
            {
                "name": "5-4-3-2-1 grounding",
                "summary": (
                    "Name 5 things you can see, 4 you can touch, 3 you can "
                    "hear, 2 you can smell, 1 you can taste. It anchors "
                    "attention in the present."
                ),
                "source_url": "https://www.nhs.uk/mental-health/feelings-symptoms-behaviours/",
            },
            {
                "name": "Cognitive defusion (ACT)",
                "summary": (
                    "Notice a thought as a thought ('I am having the thought "
                    "that I will fail') rather than as a fact. The technique "
                    "changes the relationship to the thought, not the "
                    "situation."
                ),
                "source_url": "https://www.psychologytools.com/self-help/cognitive-defusion",
            },
        ],
    },
    "sadness": {
        "label": "Sadness / low mood",
        "validation": {
            "name": "Acknowledge the loss without ranking it",
            "summary": (
                "Low mood after setbacks is common and is not a personal "
                "failure. Acknowledging the specific loss (a deadline, a "
                "rejection, an exhausted month) is more useful than a "
                "generic reassurance."
            ),
            "source_url": "https://www.nice.org.uk/guidance/cg222",
        },
        "techniques": [
            {
                "name": "Self-compassion break",
                "summary": (
                    "Say what is hard, that difficulty is not a personal "
                    "defect, and that rest is reasonable. Practised in "
                    "Kristin Neff's self-compassion work."
                ),
                "source_url": "https://self-compassion.org/",
            },
            {
                "name": "Behavioural activation: one small, doable action",
                "summary": (
                    "Pick one small, concrete action (walk, message one "
                    "person, tidy one surface) and do it within the hour. "
                    "The point is the action, not the mood."
                ),
                "source_url": "https://www.nhs.uk/mental-health/",
            },
        ],
    },
}

_BUCKET_KEYWORDS: dict[str, tuple[str, ...]] = {
    "frustration": (
        "frustrated", "frustrating", "annoyed", "annoying", "stuck", "again",
        "still not", "keeps failing", "doesn't work", "not working",
        "waste of time", "fed up", "irritating", "blocked", "cannot figure",
    ),
    "anxiety": (
        "anxious", "anxiety", "worried", "worry", "nervous", "panic",
        "scared", "afraid", "overwhelmed", "stress", "stressed", "dreading",
        "uneasy", "restless",
    ),
    "sadness": (
        "sad", "unhappy", "depressed", "down", "lonely", "hopeless",
        "exhausted", "tired of", "crying", "empty", "grief", "discouraged",
        "burned out", "burnt out", "disappointed",
    ),
}

_FALLBACK_LEXICON: dict[str, float] = {
    "good": 1.0, "great": 1.5, "fine": 0.5, "ok": 0.2, "happy": 1.2,
    "love": 1.5, "thanks": 0.8, "awesome": 1.8, "wonderful": 1.8,
    "bad": -1.0, "terrible": -1.8, "awful": -1.8, "sad": -1.2,
    "frustrated": -1.4, "anxious": -1.4, "worried": -1.0, "hate": -1.6,
    "broken": -1.2, "stuck": -1.0, "tired": -0.8, "fail": -1.3,
}


class EmpathyModule:
    """Crisis-aware supportive-responses module."""

    def __init__(self, llm: Any = None) -> None:
        self.llm = llm

    # -- step 1: crisis --------------------------------------------------- #

    def crisis_match(self, text: str) -> list[str]:
        """Return the crisis phrases detected in ``text``."""
        lowered = (text or "").lower()
        return [phrase for phrase in CRISIS_KEYWORDS if phrase in lowered]

    def detect_crisis(self, text: str) -> bool:
        return bool(self.crisis_match(text))

    def crisis_response(self) -> dict:
        """Return the crisis payload.  No other module runs after this."""
        return {
            "status": "CRISIS_RESOURCES",
            "crisis": True,
            "message": (
                "It sounds like you may be in immediate danger. Please reach "
                "out to one of the services below -- they are staffed by "
                "people who can talk this through with you."
            ),
            "resources": list(CRISIS_RESOURCES),
            "sources": [
                {"url": resource["contact"], "title": resource["name"]}
                for resource in CRISIS_RESOURCES
            ],
            "closing": MANDATORY_CLOSING,
        }

    # -- step 2: sentiment ------------------------------------------------ #

    def sentiment_buckets(self, text: str) -> dict[str, float]:
        """Bucket the message into frustration / anxiety / sadness."""
        lowered = (text or "").lower()
        scores = {bucket: 0.0 for bucket in ("frustration", "anxiety", "sadness")}
        for bucket, keywords in _BUCKET_KEYWORDS.items():
            for keyword in keywords:
                if keyword in lowered:
                    scores[bucket] += 1.0
        negativity = self._vader_negativity(text)
        if negativity is not None:
            for bucket in scores:
                scores[bucket] += negativity * 0.5
        return {bucket: round(value, 3) for bucket, value in scores.items()}

    def _vader_negativity(self, text: str) -> Optional[float]:
        """Return the VADER negative score, or ``None`` when unavailable."""
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # type: ignore
        except Exception:
            return None
        try:
            analyzer = SentimentIntensityAnalyzer()
            scores = analyzer.polarity_scores(text or "")
        except Exception:
            return None
        return float(scores.get("neg", 0.0))

    def _fallback_sentiment(self, text: str) -> float:
        words = re.findall(r"[a-z']+", (text or "").lower())
        if not words:
            return 0.0
        total = sum(_FALLBACK_LEXICON.get(word, 0.0) for word in words)
        return total / len(words)

    def primary_bucket(self, buckets: dict[str, float]) -> str:
        """Return the dominant bucket (ties break towards ``frustration``)."""
        if not buckets:
            return "frustration"
        best = max(buckets.items(), key=lambda item: (item[1], item[0] == "frustration"))
        if best[1] <= 0:
            return "frustration"
        return best[0]

    # -- step 3: frameworks ----------------------------------------------- #

    def support_plan(self, text: str) -> dict:
        """Build the sourced support plan (no LLM involved)."""
        buckets = self.sentiment_buckets(text)
        bucket = self.primary_bucket(buckets)
        framework = FRAMEWORKS[bucket]
        techniques = framework["techniques"][:2]
        sources = [
            {"title": framework["validation"]["name"], "url": framework["validation"]["source_url"]},
        ] + [
            {"title": technique["name"], "url": technique["source_url"]}
            for technique in techniques
        ]
        return {
            "status": "SUPPORTIVE_INFORMATION",
            "crisis": False,
            "bucket": bucket,
            "bucket_label": framework["label"],
            "buckets": buckets,
            "validation": framework["validation"],
            "techniques": techniques,
            "sources": sources,
            "closing": MANDATORY_CLOSING,
            "disclaimer": (
                "These are widely described self-help practices, not "
                "treatments, and no efficacy percentage is claimed."
            ),
        }

    # -- step 4: formatting ------------------------------------------------ #

    def format(self, text: str) -> str:
        """Return the final supportive message."""
        if self.detect_crisis(text):
            payload = self.crisis_response()
        else:
            payload = self.support_plan(text)
        rendered = self._render_with_llm(payload)
        if MANDATORY_CLOSING not in rendered:
            rendered = f"{rendered.rstrip()}\n\n{MANDATORY_CLOSING}"
        return rendered

    def _render_with_llm(self, payload: dict) -> str:
        """Let the language model rephrase the JSON -- nothing else."""
        if self.llm is None:
            return self._render_deterministic(payload)
        try:
            from core.caged_renderer import CagedRenderer, check_llm_output
        except Exception:
            return self._render_deterministic(payload)
        renderer = CagedRenderer(llm=self.llm)
        text = renderer.render(
            payload,
            system_prompt=(
                "YOU ARE A DETERMINISTIC TEXT RENDERER. Convert the provided "
                "JSON into a warm, calm, supportive message. ZERO "
                "HALLUCINATION: You MUST NOT add any facts, numbers, "
                "names, phone numbers, resources, techniques or conclusions "
                "not explicitly present in the JSON. Stick strictly to the "
                "schema. Never claim to be a therapist and never claim to "
                "have feelings."
            ),
        )
        verdict = check_llm_output(text, payload)
        if not verdict.get("ok", True):
            return self._render_deterministic(payload)
        return text

    def _render_deterministic(self, payload: dict) -> str:
        """Fallback rendering (no LLM available)."""
        if payload.get("crisis"):
            lines = [payload["message"], ""]
            for resource in payload["resources"]:
                lines.append(f"- {resource['name']} -- {resource['contact']}")
            return "\n".join(lines)
        validation = payload["validation"]
        lines = [
            f"It sounds like {payload['bucket_label'].lower()} is getting in "
            f"the way right now.",
            "",
            f"**{validation['name']}** -- {validation['summary']}",
            f"(source: {validation['source_url']})",
            "",
        ]
        for technique in payload["techniques"]:
            lines.append(f"**{technique['name']}** -- {technique['summary']}")
            lines.append(f"(source: {technique['source_url']})")
            lines.append("")
        lines.append(payload["disclaimer"])
        return "\n".join(lines).strip()
