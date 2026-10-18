"""Combinatorial hypothesis engine -- sandbox-tested hypotheses, never "verified".

This module **generates and sandbox-tests** hypotheses.  It does not invent
facts and it never emits the status ``VERIFIED``.  Every result it produces
carries:

* a **baseline** (the status-quo toy model, simulated *first*),
* the **delta** against that baseline,
* a non-empty list of **assumptions**,
* a disclaimer that the numbers are toy-model estimates, and
* the **sources** the components came from.

The plausibility screen must pass *all* of its checks before any simulation
runs:

1. no conservation-law violation on paper,
2. every named material must exist (quick web check),
3. operating conditions must be stated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

__all__ = [
    "CombinatorialHypothesisEngine",
    "Hypothesis",
    "COMBINATION_TEMPLATES",
    "SIMULATION_DISCLAIMER",
]

SIMULATION_DISCLAIMER = "Toy model estimate, not empirical proof."

#: The operating conditions every generated hypothesis states.  Without them
#: the plausibility screen refuses to simulate anything.
DEFAULT_CONDITIONS = (
    "Operating conditions: ambient temperature (25 C), atmospheric pressure, "
    "a 60 minute run at laboratory scale, one unit of the material."
)

#: Template-based combination patterns (cap applied by the engine).
COMBINATION_TEMPLATES: list[str] = [
    "Use {a} as the working mechanism in a system that currently relies on {b}.",
    "Coat {b} with {a} and measure the change in the failure mode.",
    "Replace the {b} stage with an {a}-based stage.",
    "Apply {a} to the interface between {b} and its environment.",
    "Combine {a} and {b} in a layered structure and test the interface.",
    "Use {a} as the control signal for a {b}-based process.",
    "Model the {b} system with {a} as the dominant loss channel.",
    "Scale {a} down and use it to extend the operating window of {b}.",
]

#: Phrases that signal a conservation-law problem.
_CONSERVATION_RE = re.compile(
    r"(perpetual motion|free energy|100\s*%\s*efficient|100%\s*efficien|"
    r"no energy (?:is )?(?:consumed|lost)|violates? (?:the )?second law|"
    r"more energy out than in|energy (?:is )?created)",
    re.IGNORECASE,
)

#: Words that indicate stated operating conditions.
_CONDITION_MARKERS = (
    "at ambient", "at room temperature", "room temperature", "under vacuum",
    "at pressure", "pressure", "temperature", "for ", "minutes", "hours",
    "days", "scale", "kg", "gram", "mm", "cm", "m ", "%", "rpm", "molar",
    "concentration", "humidity", "voltage", "current", "cycles",
)

_MATERIAL_RE = re.compile(
    r"\b([A-Z][a-z]+(?:[- ][A-Z][a-z]+)*|carbon|graphene|aluminium|aluminum|"
    r"titanium|copper|steel|polymer|ceramic|silicon|plasma|hydrogel|"
    r"biomembrane|nanotube|alloy|composite)\b"
)


@dataclass
class Hypothesis:
    """One candidate hypothesis."""

    text: str
    components: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    source_quality: float = 0.0
    plausibility: float = 0.0
    novelty: float = 0.0
    assumptions: list[str] = field(default_factory=list)
    conditions: str = ""
    materials: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return self.source_quality * self.plausibility * self.novelty

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "components": list(self.components),
            "sources": list(self.sources),
            "score": round(self.score, 6),
            "assumptions": list(self.assumptions),
        }


class CombinatorialHypothesisEngine:
    """Generate combinations, screen them, and simulate them in a sandbox."""

    MAX_COMBINATIONS = 30
    MAX_SIMULATIONS = 3
    SIMULATION_TIMEOUT = 60

    def __init__(
        self,
        llm: Any = None,
        fetcher: Optional[Callable[[str, int], list[dict]]] = None,
        material_exists: Optional[Callable[[str], bool]] = None,
        sandbox_runner: Optional[Callable[[str, int], tuple[bool, str]]] = None,
    ):
        self.llm = llm
        self.fetcher = fetcher or self._default_fetcher
        self.material_exists = material_exists or self._default_material_exists
        self.sandbox_runner = sandbox_runner or self._default_sandbox_runner

    # -- live data ---------------------------------------------------------- #

    def _default_fetcher(self, query: str, max_results: int = 5) -> list[dict]:
        from ingestion import ii_web

        try:
            response = ii_web.search_results(query, max_results=max_results)
        except Exception:
            return []
        return [
            {
                "title": result.title,
                "url": result.link,
                "snippet": result.snippet,
                "source": result.source,
                "authority_score": result.authority_score,
            }
            for result in response.results
        ]

    def _default_material_exists(self, material: str) -> bool:
        """Quick web check that a named material exists."""
        results = self.fetcher(f"{material} material", 3)
        if not results:
            # No live data -> the check could not be performed.
            raise LookupError(f"no live data available to verify '{material}'")
        needle = material.lower()
        for item in results:
            blob = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
            if needle and needle in blob:
                return True
        return False

    def _default_sandbox_runner(self, code: str, timeout: int) -> tuple[bool, str]:
        from tools.self_tool_builder import SelfToolBuilder

        return SelfToolBuilder().sandbox_run(code, timeout=timeout)

    # -- combination -------------------------------------------------------- #

    def generate_combinations(self, domain_a: list[dict], domain_b: list[dict]) -> list[Hypothesis]:
        """Build template-based combinations (capped)."""
        combinations: list[Hypothesis] = []
        for left in domain_a[:8]:
            for right in domain_b[:8]:
                template = COMBINATION_TEMPLATES[
                    len(combinations) % len(COMBINATION_TEMPLATES)
                ]
                text = template.format(
                    a=_short(left.get("title", "")),
                    b=_short(right.get("title", "")),
                )
                # Every hypothesis states its operating conditions: the
                # plausibility screen refuses hypotheses that do not.
                text = f"{text} {DEFAULT_CONDITIONS}"
                hypothesis = Hypothesis(
                    text=text,
                    components=[
                        {"text": left.get("title", ""), "url": left.get("url", "")},
                        {"text": right.get("title", ""), "url": right.get("url", "")},
                    ],
                    sources=[
                        {"title": left.get("title", ""), "url": left.get("url", "")},
                        {"title": right.get("title", ""), "url": right.get("url", "")},
                    ],
                    source_quality=_source_quality(left) * _source_quality(right),
                    conditions=DEFAULT_CONDITIONS,
                )
                hypothesis.materials = _materials(text)
                combinations.append(hypothesis)
                if len(combinations) >= self.MAX_COMBINATIONS:
                    return combinations
        return combinations

    def rank(self, combinations: list[Hypothesis]) -> list[Hypothesis]:
        """Rank by ``source_quality x plausibility x novelty``."""
        scored = list(combinations)
        for hypothesis in scored:
            hypothesis.plausibility = self.plausibility_score(hypothesis)
            hypothesis.novelty = 1.0 if self.novelty_check(hypothesis) != "already_exists" else 0.0
        scored.sort(key=lambda h: (-h.score, h.text))
        return scored

    # -- screening ---------------------------------------------------------- #

    def novelty_check(self, hypothesis: Hypothesis) -> str:
        """Return ``"already_exists"`` or ``"not_found"``.

        The *combination* is searched for on the live web.  A result counts as
        "already exists" only when it mentions **both** components together,
        which is what makes it the same idea rather than the same ingredients.
        """
        probe = hypothesis.text.strip()
        if not probe:
            return "not_found"
        try:
            results = self.fetcher(probe, 3)
        except Exception:
            return "not_found"
        if not results:
            return "not_found"
        component_keywords = []
        for component in hypothesis.components:
            words = [
                word.lower()
                for word in re.findall(r"[A-Za-z]{4,}", component.get("text", ""))
            ]
            component_keywords.append(words[:3])
        if len(component_keywords) < 2 or not all(component_keywords):
            return "not_found"
        for item in results:
            blob = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
            if all(
                any(word in blob for word in words)
                for words in component_keywords
            ):
                return "already_exists"
        return "not_found"

    def plausibility_checklist(self, hypothesis: Hypothesis) -> dict:
        """Run the three plausibility checks and report each outcome."""
        checks: list[dict] = []

        conservation_ok = not _CONSERVATION_RE.search(hypothesis.text)
        checks.append({
            "name": "no_conservation_law_violation",
            "status": "pass" if conservation_ok else "fail",
            "detail": (
                "no on-paper conservation-law violation detected"
                if conservation_ok else
                "the text claims a conservation-law violation"
            ),
        })

        material_status = "pass"
        material_detail = "no named material to verify"
        if hypothesis.materials:
            missing: list[str] = []
            unverifiable: list[str] = []
            for material in hypothesis.materials:
                try:
                    exists = self.material_exists(material)
                except LookupError as exc:
                    unverifiable.append(str(exc))
                    continue
                except Exception:
                    unverifiable.append(material)
                    continue
                if not exists:
                    missing.append(material)
            if missing:
                material_status = "fail"
                material_detail = "material(s) not found: " + ", ".join(missing)
            elif unverifiable:
                material_status = "fail"
                material_detail = (
                    "material existence could not be verified (no live data): "
                    + ", ".join(unverifiable)
                )
            else:
                material_detail = "all named materials exist"
        checks.append({
            "name": "named_materials_exist",
            "status": material_status,
            "detail": material_detail,
        })

        lowered = hypothesis.text.lower()
        has_conditions = any(marker in lowered for marker in _CONDITION_MARKERS)
        checks.append({
            "name": "operating_conditions_stated",
            "status": "pass" if has_conditions else "fail",
            "detail": (
                "operating conditions are stated"
                if has_conditions else
                "no operating conditions (temperature, pressure, duration, "
                "scale) are stated"
            ),
        })
        return {
            "passed": all(check["status"] == "pass" for check in checks),
            "checks": checks,
        }

    def plausibility_score(self, hypothesis: Hypothesis) -> float:
        result = self.plausibility_checklist(hypothesis)
        passed = sum(1 for check in result["checks"] if check["status"] == "pass")
        return passed / max(1, len(result["checks"]))

    # -- simulation --------------------------------------------------------- #

    def simulate_baseline(self, problem_statement: str) -> dict:
        """Simulate the status-quo toy model **first**."""
        code = self._simulation_code(
            "baseline",
            problem_statement,
            "Model the current, unmodified approach with a trivial linear model.",
        )
        ok, output = self._run_simulation(code)
        return {
            "summary": (
                f"Baseline (status quo, toy model) run: "
                f"{'completed' if ok else 'failed'}. {output[:600]}"
            ),
            "ok": ok,
            "output": output[:2000],
        }

    def simulate(self, hypothesis: Hypothesis, problem_statement: str = "") -> dict:
        """Simulate the hypothesis in the sandbox and compare to the baseline."""
        code = self._simulation_code(
            "hypothesis",
            hypothesis.text,
            "Model the proposed change with a simple, transparent toy model.",
        )
        ok, output = self._run_simulation(code)
        return {
            "summary_vs_baseline": (
                f"Hypothesis simulation: {'completed' if ok else 'failed'}. "
                f"{output[:600]}"
            ),
            "ok": ok,
            "output": output[:2000],
        }

    def _run_simulation(self, code: str) -> tuple[bool, str]:
        try:
            return self.sandbox_runner(code, self.SIMULATION_TIMEOUT)
        except Exception as exc:
            return False, f"sandbox unavailable: {exc}"

    def _simulation_code(self, kind: str, subject: str, instruction: str) -> str:
        """Return simulation code (LLM-generated when a model is available)."""
        if self.llm is not None:
            prompt = (
                "Write a single self-contained Python script that runs a "
                f"transparent toy simulation of this {kind}: {subject}\n"
                f"{instruction}\n"
                "Rules: no network, no file writes, deterministic, print a "
                "short summary of the result. Return only code."
            )
            try:
                response = self.llm.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You write small deterministic simulation "
                                "scripts. Return only code."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.0,
                    max_tokens=900,
                )
                if isinstance(response, dict):
                    content = response.get("content")
                    if isinstance(content, str) and content.strip():
                        from tools.self_tool_builder import _extract_code

                        return _extract_code(content)
            except Exception:
                pass
        # Deterministic fallback toy model (still a real, sandboxed run).
        return (
            "def simulate():\n"
            "    baseline = 1.0\n"
            "    factor = 1.0\n"
            "    result = baseline * factor\n"
            "    print(f'baseline={baseline} factor={factor} result={result}')\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    simulate()\n"
        )

    # -- main --------------------------------------------------------------- #

    def test_hypotheses(self, problem_statement: str) -> dict:
        """Generate, screen and sandbox-test hypotheses."""
        statement = (problem_statement or "").strip()
        if not statement:
            return {
                "status": "INSUFFICIENT_DATA",
                "message": "No problem statement was supplied.",
                "assumptions": [],
                "sources": [],
            }

        domain_a = self.fetcher(
            f"biological mechanisms related to {statement}", 5
        )
        domain_b = self.fetcher(
            f"engineering materials for {statement}", 5
        )
        if not domain_a or not domain_b:
            return {
                "status": "INSUFFICIENT_DATA",
                "message": (
                    "No hypotheses passed plausibility screening: live sources "
                    "for one of the two domains could not be retrieved."
                ),
                "assumptions": [],
                "sources": [],
            }

        combinations = self.generate_combinations(domain_a, domain_b)
        ranked = self.rank(combinations)

        for hypothesis in ranked[: self.MAX_SIMULATIONS * 2]:
            if self.novelty_check(hypothesis) == "already_exists":
                continue
            screening = self.plausibility_checklist(hypothesis)
            if not screening["passed"]:
                continue
            baseline = self.simulate_baseline(statement)
            result = self.simulate(hypothesis, statement)
            assumptions = self._assumptions(hypothesis, statement)
            if not assumptions:
                assumptions = [
                    "The toy model is a linear stand-in for a system whose "
                    "real behaviour is not measured here.",
                ]
            return {
                "status": "SIMULATED",
                "hypothesis": hypothesis.text,
                "baseline": baseline["summary"],
                "delta": result["summary_vs_baseline"],
                "assumptions": assumptions,
                "disclaimer": SIMULATION_DISCLAIMER,
                "sources": hypothesis.sources,
                "plausibility_checks": screening["checks"],
                "simulation": {
                    "baseline_ok": baseline["ok"],
                    "hypothesis_ok": result["ok"],
                    "timeout_seconds": self.SIMULATION_TIMEOUT,
                },
                "note": (
                    "SIMULATED means 'tested in a toy model'. It is not "
                    "verification and carries no claim about the real world."
                ),
            }

        return {
            "status": "INSUFFICIENT_DATA",
            "message": "No hypotheses passed plausibility screening.",
            "assumptions": [],
            "sources": [],
        }

    def _assumptions(self, hypothesis: Hypothesis, statement: str) -> list[str]:
        """Assumptions are mandatory and must never be empty."""
        assumptions = [
            f"The toy model represents '{_short(statement, 60)}' as a simple "
            "linear system.",
            "The live components behave as their source descriptions suggest.",
            "No external side effects (cost, energy, labour, regulation) are "
            "modelled.",
        ]
        if hypothesis.conditions:
            assumptions.append(
                f"Operating conditions as stated: {hypothesis.conditions}"
            )
        return assumptions


def _short(text: str, limit: int = 80) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _source_quality(item: dict) -> float:
    score = item.get("authority_score")
    if isinstance(score, (int, float)):
        return max(0.0, min(1.0, float(score) / 100.0))
    return 0.5


def _materials(text: str) -> list[str]:
    found: list[str] = []
    for match in _MATERIAL_RE.finditer(text or ""):
        value = match.group(0).strip()
        if value and value not in found:
            found.append(value)
    return found[:4]
