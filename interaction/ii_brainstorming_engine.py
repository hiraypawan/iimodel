"""Brainstorming engine -- mechanical recombination, explicitly not invention.

What this module does
---------------------
1. Fetches **live** search results for a topic.
2. Applies SCAMPER operators -- written out as explicit Python functions -- to
   those results.
3. Returns at most three recombinations.

What this module does **not** do
--------------------------------
It does not invent. A recombination is a *mechanical* transformation of two
real, cited components.  The combination itself has no evidence behind it, so
every result carries:

* ``component_evidence`` -- URLs proving that each component exists,
* ``combination_status: "UNVALIDATED_COMBINATION"`` -- the combination is
  unvalidated, and the output never implies otherwise.

The language model only formats the resulting JSON.  It may not add ideas.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

__all__ = [
    "BrainstormingEngine",
    "SCAMPER_OPERATORS",
    "COMBINATION_STATUS",
]

COMBINATION_STATUS = "UNVALIDATED_COMBINATION"

DISCLAIMER = (
    "These are mechanical recombinations of cited components (SCAMPER "
    "operators applied to live search results). The components exist and are "
    "cited; the combination itself has not been tested, built, or validated."
)


def _short(text: str, limit: int = 90) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------- #
# SCAMPER operators -- explicit functions, one per operator
# --------------------------------------------------------------------------- #

def _op_substitute(left: str, right: str) -> str:
    return f"Substitute the core mechanism of {right} into {left}."


def _op_combine(left: str, right: str) -> str:
    return f"Combine {left} with {right} into a single workflow."


def _op_adapt(left: str, right: str) -> str:
    return f"Adapt {left} to the constraints that {right} works under."


def _op_modify(left: str, right: str) -> str:
    return f"Modify {left} (scale, cost or speed it up) the way {right} does."


def _op_put_to_other_use(left: str, right: str) -> str:
    return f"Use {left} for a purpose it is not normally used for, in the "
    f"context of {right}."


def _op_eliminate(left: str, right: str) -> str:
    return f"Eliminate the main constraint of {right} from {left}."


def _op_reverse(left: str, right: str) -> str:
    return f"Reverse the order of {left} so that it feeds {right} instead of "
    f"following it."


#: Operator name -> explicit transformation function.
SCAMPER_OPERATORS: dict[str, Callable[[str, str], str]] = {
    "substitute": _op_substitute,
    "combine": _op_combine,
    "adapt": _op_adapt,
    "modify": _op_modify,
    "put_to_other_use": _op_put_to_other_use,
    "eliminate": _op_eliminate,
    "reverse": _op_reverse,
}


class BrainstormingEngine:
    """Fetch live components and recombine them mechanically."""

    MAX_OUTPUTS = 3

    def __init__(self, llm: Any = None, fetcher: Optional[Callable] = None) -> None:
        self.llm = llm
        self.fetcher = fetcher or self._default_fetcher

    # -- live components --------------------------------------------------- #

    def _default_fetcher(self, topic: str) -> list[dict]:
        from ingestion import ii_web

        try:
            response = ii_web.search_results(topic, max_results=5)
        except Exception:
            return []
        return [
            {
                "title": result.title,
                "url": result.link,
                "snippet": result.snippet,
            }
            for result in response.results
        ]

    def fetch_trends(self, topic: str) -> list[dict]:
        """Fetch live components for ``topic`` (each with its URL)."""
        raw = self.fetcher(topic) or []
        trends: list[dict] = []
        seen: set[str] = set()
        for item in raw:
            url = item.get("url") or item.get("link") or ""
            title = _short(item.get("title") or item.get("snippet") or "")
            if not title:
                continue
            key = url or title.lower()
            if key in seen:
                continue
            seen.add(key)
            trends.append({
                "title": title,
                "url": url,
                "snippet": _short(item.get("snippet") or ""),
            })
        return trends

    # -- recombination ------------------------------------------------------ #

    def generate_combinations(self, trends: list[dict]) -> list[dict]:
        """Apply the SCAMPER operators to pairs of components.

        The output is a mechanical transformation, never a new idea.
        """
        combinations: list[dict] = []
        if len(trends) < 2:
            return combinations
        operator_names = list(SCAMPER_OPERATORS.keys())
        index = 0
        for i in range(len(trends)):
            for j in range(i + 1, len(trends)):
                left = trends[i]
                right = trends[j]
                operator = operator_names[index % len(operator_names)]
                index += 1
                function = SCAMPER_OPERATORS[operator]
                idea = function(left["title"], right["title"])
                combinations.append({
                    "idea": idea,
                    "operator": operator,
                    "components": [
                        {"text": left["title"], "url": left["url"]},
                        {"text": right["title"], "url": right["url"]},
                    ],
                    "component_evidence": [
                        {"claim": left["title"], "url": left["url"]},
                        {"claim": right["title"], "url": right["url"]},
                    ],
                })
                if len(combinations) >= self.MAX_OUTPUTS:
                    return combinations
        return combinations

    # -- public API -------------------------------------------------------- #

    def generate(self, topic: str) -> dict:
        """Produce the brainstorm payload for ``topic``."""
        trends = self.fetch_trends(topic)
        if len(trends) < 2:
            return {
                "status": "INSUFFICIENT_DATA",
                "topic": topic,
                "message": (
                    "Fewer than two live components could be retrieved, so no "
                    "recombination was produced. Nothing was invented to fill "
                    "the gap."
                ),
                "outputs": [],
                "component_evidence": [],
                "combination_status": COMBINATION_STATUS,
                "disclaimer": DISCLAIMER,
            }
        combinations = self.generate_combinations(trends)
        evidence: list[dict] = []
        seen_urls: set[str] = set()
        for combination in combinations:
            for item in combination["component_evidence"]:
                url = item.get("url") or ""
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                evidence.append(item)
        return {
            "status": COMBINATION_STATUS,
            "topic": topic,
            "method": (
                "Mechanical recombination: SCAMPER operators applied to live "
                "search results. No invention, no novelty claim."
            ),
            "outputs": combinations,
            "component_evidence": evidence,
            "combination_status": COMBINATION_STATUS,
            "disclaimer": DISCLAIMER,
        }

    def format(self, topic: str) -> str:
        """Return the rendered brainstorm message."""
        payload = self.generate(topic)
        if self.llm is not None and payload.get("outputs"):
            try:
                from core.caged_renderer import CagedRenderer, check_llm_output

                renderer = CagedRenderer(llm=self.llm)
                text = renderer.render(
                    payload,
                    system_prompt=(
                        "YOU ARE A DETERMINISTIC TEXT RENDERER. Convert the "
                        "provided JSON into a short, readable list. ZERO "
                        "HALLUCINATION: You MUST NOT add any facts, ideas, "
                        "names or conclusions not explicitly present in the "
                        "JSON. Stick strictly to the schema. Do not add "
                        "ideas of your own."
                    ),
                )
                if check_llm_output(text, payload).get("ok", True):
                    return text
            except Exception:
                pass
        return self._render_deterministic(payload)

    def _render_deterministic(self, payload: dict) -> str:
        if payload.get("status") == "INSUFFICIENT_DATA":
            return payload["message"]
        lines = [
            f"Brainstorm for '{payload['topic']}' -- {payload['method']}",
            "",
        ]
        for index, output in enumerate(payload["outputs"], 1):
            lines.append(f"{index}. [{output['operator']}] {output['idea']}")
            for component in output["components"]:
                lines.append(f"   component: {component['text']} -- {component['url']}")
        lines.append("")
        lines.append(f"combination_status: {payload['combination_status']}")
        lines.append(payload["disclaimer"])
        return "\n".join(lines)
