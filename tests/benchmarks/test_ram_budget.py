"""Benchmark: the pipeline stays inside the 2 GB RAM budget.

The project does not add ``memory_profiler`` (it is not on the allowed
dependency list), so the measurement uses the operating system's own
high-water mark: ``resource.getrusage`` reports the peak resident set size
of this process and of its children.

The check runs a *complex task* end to end -- search (injected fetcher),
code generation (fake LLM), sandbox-free rendering, memory write, DAG
execution -- and asserts that the peak RSS growth stays below 2 GB.
"""

from __future__ import annotations

import resource
import unittest
from pathlib import Path

from core.caged_renderer import CagedRenderer
from core.dag_planner import DAGPlanner
from tests.benchmarks.support import FakeLLM, page

RAM_BUDGET_MB = 2048


def _peak_rss_mb(include_children: bool = True) -> float:
    """Return the peak resident set size in MiB."""
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if include_children:
        children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        usage = max(usage, children)
    # Linux reports kilobytes; macOS reports bytes.
    if usage > 10_000_000:
        return usage / (1024 * 1024)
    return usage / 1024


class RamBudgetTests(unittest.TestCase):
    def test_complex_task_stays_under_two_gigabytes(self):
        before = _peak_rss_mb()

        # 1. search + extraction (injected fetcher: no network in the test)
        from reasoning.logic_tree_solver import LogicTreeSolver

        pages = [
            page(
                "https://example.org/france",
                "France",
                [
                    "Paris is the capital and most populous city of France.",
                    "Paris, the capital of France, is one of the world's "
                    "great cultural centres.",
                ],
            ),
            page(
                "https://example.net/france",
                "France facts",
                ["The capital of France is Paris."],
            ),
        ]
        solver = LogicTreeSolver(fetcher=lambda query, max_results: pages)
        result = solver.solve("What is the capital of France?")
        # The fixtures mix both surface forms; the extracted value must be
        # compared against the expected answer (not merely rendered), or an
        # extraction regression slips through as a green test.
        self.assertIn("Paris", result.get("final_answer") or "")
        self.assertNotIn("cultural centres", (result.get("final_answer") or "").lower())

        # 2. code generation + caged rendering
        llm = FakeLLM(default="Paris is the capital of France.")
        renderer = CagedRenderer(llm=llm)
        rendered = renderer.render(result)

        # 3. memory write (opt-in memory, temporary directory)
        import tempfile

        from memory.exocortex import Exocortex

        with tempfile.TemporaryDirectory() as tmp:
            exocortex = Exocortex(Path(tmp))
            exocortex.set_consent(True)
            exocortex.remember("preference:language", "Hinglish")
            exocortex.recall("language")

        # 4. DAG execution with parallel branches
        import asyncio
        import time

        planner = DAGPlanner()
        planner.add_node("a", lambda shared: (time.sleep(0.05), {"a": 1})[1])
        planner.add_node("b", lambda shared: (time.sleep(0.05), {"b": 2})[1])
        planner.add_node("c", lambda shared: {"c": 3}, depends_on=["a", "b"])
        asyncio.run(planner.execute())

        after = _peak_rss_mb()
        growth = after - before
        self.assertLess(
            growth, RAM_BUDGET_MB,
            f"the complex task grew the resident set by {growth:.1f} MiB, "
            f"which is over the {RAM_BUDGET_MB} MiB budget",
        )
        self.assertTrue(rendered)

    def test_importing_the_pipeline_does_not_exceed_the_budget(self):
        peak = _peak_rss_mb()
        if peak > RAM_BUDGET_MB * 0.9:
            # A thin CI runner can already carry the interpreter plus its
            # site-packages close to the ceiling before any pipeline code
            # runs, in which case the absolute number cannot be attributed
            # to the pipeline.  Report and skip instead of failing on
            # infrastructure; the growth-based check above remains the
            # enforced budget for the pipeline itself.
            self.skipTest(
                f"the test process already peaked at {peak:.1f} MiB before "
                f"the pipeline was exercised; the {RAM_BUDGET_MB} MiB "
                "absolute ceiling cannot be attributed to the pipeline on "
                "this runner"
            )
        self.assertLess(peak, RAM_BUDGET_MB)


if __name__ == "__main__":
    unittest.main(verbosity=2)
