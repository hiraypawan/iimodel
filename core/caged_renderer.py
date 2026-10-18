"""Caged renderer + hallucination detector.

The 1.5B model in this project has exactly one job: **render** structured
JSON produced by deterministic Python algorithms into natural language.  It is
caged twice:

1. by the strict system prompt (the model is told to stick to the JSON), and
2. by the **hallucination detector** (entity diff), which compares the model's
   output against the JSON it was given.

If the detector rejects the output, the renderer discards it, retries once,
and finally falls back to a formatted dump of the raw JSON.  Every rejection
is logged.

Detector limits (v1)
--------------------
The detector is an **entity diff**.  It catches injected numbers, dates,
names and organisations.  It does *not* catch:

* relation flips ("correlates with" -> "causes"),
* negation or qualifier changes ("may" -> "must", "some" -> "all"),
* paraphrase false positives (a legitimate rewording that looks like a new
  entity to the regex fallback).

These blind spots are documented rather than hidden.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from core.nlp import extract_entities, normalize_number

__all__ = [
    "STRICT_SYSTEM_PROMPT",
    "CagedRenderer",
    "check_llm_output",
    "is_generative_backend",
    "REJECTION_LOG",
    "BLIND_SPOTS",
]

#: The exact renderer system prompt.  Do not reword it casually: the wording
#: is part of the contract with the model.
STRICT_SYSTEM_PROMPT = (
    "YOU ARE A DETERMINISTIC TEXT RENDERER. Convert the provided JSON into "
    "natural, fluent Hinglish/English. ZERO HALLUCINATION: You MUST NOT add "
    "any facts, numbers, or names not explicitly present in the JSON. Stick "
    "strictly to the schema."
)

#: Known blind spots of the entity-diff detector (documented, not hidden).
BLIND_SPOTS = (
    "relation flips (e.g. 'correlates with' rendered as 'causes')",
    "negation and qualifier changes (e.g. 'may' rendered as 'must')",
    "paraphrase false positives (legitimate rewording that looks like a new entity)",
)

#: Every rejected rendering is appended here (and returned by the renderer).
REJECTION_LOG: list[dict] = []


# --------------------------------------------------------------------------- #
# Hallucination detector
# --------------------------------------------------------------------------- #

def _normalize_entity(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _numeric_core(text: str) -> Optional[str]:
    match = re.search(r"\d[\d,]*(?:\.\d+)?", text or "")
    if not match:
        return None
    return normalize_number(match.group(0))


def _entities_of(text: str) -> list[dict]:
    return extract_entities(text or "")


def is_generative_backend(llm: Any) -> bool:
    """Return ``True`` when ``llm`` can actually generate text.

    The bundled demo placeholder (``LocalModel`` in ``demo`` mode) returns
    canned sentences.  Those are not renderings of the JSON, and the
    entity-diff detector would -- correctly -- reject them, so the cage
    falls back to the deterministic renderer instead.
    """
    if llm is None:
        return False
    mode = getattr(llm, "mode", None)
    if isinstance(mode, str) and mode.strip().lower() == "demo":
        return False
    return True


def check_llm_output(llm_text: str, source_json: dict) -> dict:
    """Compare the rendered text with the JSON it was rendered from.

    Returns ``{"ok", "added_entities", "note"}``.

    ``ok`` is ``False`` when the rendering contains an entity (number, date,
    name, organisation) that does not appear in the source JSON.
    """
    source_text = json.dumps(source_json, ensure_ascii=False, sort_keys=True, default=str)
    source_entities = _entities_of(source_text)
    source_normalized = {
        _normalize_entity(entity["text"]) for entity in source_entities
    }
    source_numbers = {
        _numeric_core(entity["text"]) for entity in source_entities
    }
    source_numbers.discard(None)
    source_blob = _normalize_entity(source_text)

    added: list[str] = []
    for entity in _entities_of(llm_text or ""):
        raw = entity.get("text", "")
        normalized = _normalize_entity(raw)
        if not normalized:
            continue
        if normalized in source_normalized:
            continue
        core = _numeric_core(raw)
        if core and core in source_numbers:
            continue
        if normalized and normalized in source_blob:
            continue
        if core and core in source_blob:
            continue
        if normalized not in added:
            added.append(raw)

    note = (
        "Entity diff only. It cannot see relation flips, negation/qualifier "
        "changes, or paraphrase false positives."
    )
    return {
        "ok": not added,
        "added_entities": added,
        "note": note,
        "blind_spots": list(BLIND_SPOTS),
    }


# --------------------------------------------------------------------------- #
# Renderer
# --------------------------------------------------------------------------- #

class CagedRenderer:
    """Render structured JSON into natural language inside a cage."""

    def __init__(
        self,
        llm: Any = None,
        system_prompt: str = STRICT_SYSTEM_PROMPT,
        *,
        max_retries: int = 1,
    ):
        self.llm = llm if is_generative_backend(llm) else None
        self.system_prompt = system_prompt
        self.max_retries = max(0, int(max_retries))
        self.rejections: list[dict] = []

    # -- public API -------------------------------------------------------- #

    def format(self, raw: dict) -> str:
        """Render ``raw`` (the router's entry point)."""
        return self.render(raw)

    def render(
        self,
        raw: dict,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Render ``raw`` into text, caging the model output."""
        prompt = system_prompt or self.system_prompt
        if self.llm is None:
            return self._render_deterministic(raw)
        attempt = 0
        while True:
            text = self._call_llm(raw, prompt)
            if text is None:
                return self._render_deterministic(raw)
            verdict = check_llm_output(text, raw)
            if verdict["ok"]:
                return text
            entry = {
                "attempt": attempt + 1,
                "added_entities": verdict["added_entities"],
                "note": verdict["note"],
            }
            self.rejections.append(entry)
            REJECTION_LOG.append(entry)
            attempt += 1
            if attempt > self.max_retries:
                return self._render_json_fallback(raw)
            prompt = (
                f"{prompt}\n\nYour previous answer was rejected because it "
                f"contained entities that are not in the JSON: "
                f"{', '.join(verdict['added_entities'])}. Rewrite the answer "
                f"using only the JSON."
            )

    # -- internals --------------------------------------------------------- #

    def _call_llm(self, raw: dict, system_prompt: str) -> Optional[str]:
        try:
            response = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            "Render this JSON as natural text:\n"
                            + json.dumps(raw, ensure_ascii=False, indent=2, default=str)
                        ),
                    },
                ],
                temperature=0.0,
                max_tokens=1200,
            )
        except Exception:
            return None
        if isinstance(response, dict):
            content = response.get("content")
            if isinstance(content, str):
                return content.strip()
        return None

    def _render_json_fallback(self, raw: dict) -> str:
        """Last-resort rendering: a readable dump of the raw JSON."""
        return (
            "The rendering was rejected by the hallucination detector, so the "
            "raw structured result is shown instead:\n\n"
            + json.dumps(raw, ensure_ascii=False, indent=2, default=str)
        )

    def _render_deterministic(self, raw: dict) -> str:
        """Deterministic rendering used when no model backend is available."""
        if not isinstance(raw, dict):
            return str(raw)
        lines: list[str] = []
        status = raw.get("status")
        if status:
            lines.append(f"Status: {status}")
        answer = raw.get("final_answer") or raw.get("answer")
        if answer:
            lines.append("")
            lines.append(str(answer))
        for key in ("message", "note", "disclaimer", "closing"):
            value = raw.get(key)
            if isinstance(value, str) and value:
                lines.append("")
                lines.append(value)
        sources = raw.get("sources")
        if isinstance(sources, list) and sources:
            lines.append("")
            lines.append("Sources:")
            for index, source in enumerate(sources, 1):
                if isinstance(source, dict):
                    url = source.get("url", "")
                    title = source.get("title") or source.get("claim") or ""
                    lines.append(f"[{index}] {title} -- {url}".rstrip(" -"))
                else:
                    lines.append(f"[{index}] {source}")
        missing = raw.get("missing")
        if isinstance(missing, list) and missing:
            lines.append("")
            lines.append("Still missing:")
            for item in missing:
                lines.append(f"- {item}")
        return "\n".join(lines).strip()
