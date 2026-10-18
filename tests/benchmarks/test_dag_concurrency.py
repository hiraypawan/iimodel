"""Benchmark: independent DAG nodes really do run concurrently.

The assertion is made on the **event log**, not on wall-clock time: two
independent nodes must have overlapping ``start``/``end`` intervals, and a
dependent node must start only after its dependency ended.

Timings are deliberately generous so the test cannot be flaky on a loaded
machine.
"""

from __future__ import annotations

import asyncio
import time
import unittest

from core.dag_planner import DAGPlanner


def _interval(log: list[dict], node_id: str) -> tuple[float, float]:
    start = None
    end = None
    for event in log:
        if event["node"] != node_id:
            continue
        if event["event"] == "start":
            start = event["ts"]
        elif event["event"] == "end":
            end = event["ts"]
    assert start is not None and end is not None, f"no event pair for {node_id}"
    return start, end


class DagConcurrencyTests(unittest.TestCase):
    def test_independent_nodes_overlap_in_the_event_log(self):
        planner = DAGPlanner()

        def slow_a(shared: dict) -> dict:
            time.sleep(0.25)
            return {"a": True}

        def slow_b(shared: dict) -> dict:
            time.sleep(0.25)
            return {"b": True}

        planner.add_node("a", slow_a)
        planner.add_node("b", slow_b)
        planner.add_node("c", lambda shared: {"c": True}, depends_on=["a", "b"])

        outcome = asyncio.run(planner.execute())

        self.assertEqual(outcome["status"], "ok", outcome.get("errors"))
        log = outcome["event_log"]

        start_a, end_a = _interval(log, "a")
        start_b, end_b = _interval(log, "b")
        start_c, _end_c = _interval(log, "c")

        self.assertLess(
            start_b, end_a,
            "node b must start while node a is still running",
        )
        self.assertLess(
            start_a, end_b,
            "node a must start while node b is still running",
        )
        self.assertGreaterEqual(
            start_c, end_a,
            "a dependent node must start after its dependency ended",
        )
        self.assertGreaterEqual(
            start_c, end_b,
            "a dependent node must start after its dependency ended",
        )

    def test_event_log_records_start_and_end_for_every_node(self):
        planner = DAGPlanner()
        planner.add_node("only", lambda shared: {"ok": True})
        outcome = asyncio.run(planner.execute())
        events = {(event["node"], event["event"]) for event in outcome["event_log"]}
        self.assertIn(("only", "start"), events)
        self.assertIn(("only", "end"), events)

    def test_level_structure_is_reported(self):
        planner = DAGPlanner()
        planner.add_node("a", lambda shared: {"a": 1})
        planner.add_node("b", lambda shared: {"b": 1})
        planner.add_node("c", lambda shared: {"c": 1}, depends_on=["a", "b"])
        outcome = asyncio.run(planner.execute())
        self.assertEqual(outcome["levels"][0], ["a", "b"])
        self.assertEqual(outcome["levels"][1], ["c"])

    def test_failing_node_is_reported_without_breaking_the_run(self):
        planner = DAGPlanner()

        def boom(shared: dict) -> dict:
            raise RuntimeError("boom")

        planner.add_node("ok", lambda shared: {"ok": True})
        planner.add_node("bad", boom)
        outcome = asyncio.run(planner.execute())
        self.assertEqual(outcome["status"], "partial")
        self.assertIn("bad", outcome["errors"])
        self.assertIn("boom", outcome["errors"]["bad"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
