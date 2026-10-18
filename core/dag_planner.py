"""DAG planner -- deterministic task orchestration with parallel branches.

The planner turns a task into a ``networkx.DiGraph``:

* independent nodes run **concurrently** (asyncio),
* dependent nodes are gated by the topological order,
* every node start/end is written to an **event log**.

The event log -- not wall-clock timing -- is what the concurrency benchmark
asserts on: comparing stopwatch values is flaky, comparing *intervals that
overlap* is not.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

try:  # pragma: no cover - networkx is a hard dependency of the project
    import networkx as nx
except Exception:  # pragma: no cover
    nx = None  # type: ignore[assignment]

__all__ = ["Node", "DAGPlanner", "PLAN_TEMPLATES"]

NodeFn = Callable[..., Any]


@dataclass
class Node:
    """One unit of work in the execution graph."""

    id: str
    name: str
    fn: NodeFn
    depends_on: list[str] = field(default_factory=list)
    is_async: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "depends_on": list(self.depends_on),
            "is_async": self.is_async,
        }


#: Declarative plan templates.  Each entry maps an intent to the node graph
#: the router builds.  Node functions are bound by the router; the planner
#: only owns the *shape* of the plan and its execution order.
PLAN_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "logic_tree": {
        "solve": [],
        "finalize": ["solve"],
    },
    "research": {
        "web_fetch": [],
        "memory_recall": [],
        "merge": ["web_fetch", "memory_recall"],
        "finalize": ["merge"],
    },
    "code": {
        "spec": [],
        "generate": ["spec"],
        "sandbox_run": ["generate"],
        "critic": ["sandbox_run"],
        "finalize": ["critic"],
    },
    "empathy": {
        "crisis_scan": [],
        "sentiment": [],
        "frameworks": ["sentiment"],
        "finalize": ["frameworks"],
    },
    "brainstorm": {
        "trends": [],
        "scamper": ["trends"],
        "finalize": ["scamper"],
    },
    "hypothesis": {
        "domain_a": [],
        "domain_b": [],
        "combine": ["domain_a", "domain_b"],
        "baseline": ["combine"],
        "simulate": ["baseline"],
        "finalize": ["simulate"],
    },
    "chat": {
        "finalize": [],
    },
}


class DAGPlanner:
    """Build and execute task graphs."""

    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._event_log: list[dict] = []
        self._counter = 0

    # -- graph construction ------------------------------------------------ #

    def add_node(
        self,
        node_id: str,
        fn: NodeFn,
        *,
        name: Optional[str] = None,
        depends_on: Optional[list[str]] = None,
    ) -> Node:
        """Register a node in the working plan."""
        node = Node(
            id=node_id,
            name=name or node_id,
            fn=fn,
            depends_on=list(depends_on or []),
            is_async=inspect.iscoroutinefunction(fn),
        )
        self._nodes[node_id] = node
        return node

    def create_execution_graph(
        self,
        intent: str,
        *,
        node_functions: Optional[dict[str, NodeFn]] = None,
    ):
        """Create the execution graph for ``intent``.

        ``node_functions`` maps node ids to callables.  Nodes without a
        function are still present in the graph (so the shape stays
        inspectable) but are skipped during execution.
        """
        if nx is None:  # pragma: no cover
            raise RuntimeError("networkx is required to build execution graphs.")
        template = PLAN_TEMPLATES.get(intent, PLAN_TEMPLATES["chat"])
        graph = nx.DiGraph()
        functions = node_functions or {}
        for node_id, depends_on in template.items():
            graph.add_node(node_id, name=node_id)
        for node_id, depends_on in template.items():
            for dependency in depends_on:
                if dependency in graph:
                    graph.add_edge(dependency, node_id)
        self._nodes = {}
        for node_id in template:
            fn = functions.get(node_id)
            if fn is None:
                continue
            self.add_node(
                node_id,
                fn,
                name=node_id,
                depends_on=list(template[node_id]),
            )
        return graph

    # -- execution --------------------------------------------------------- #

    def event_log(self) -> list[dict]:
        """Return the recorded node start/end events."""
        return list(self._event_log)

    def reset_event_log(self) -> None:
        self._event_log = []

    def _record(self, node_id: str, event: str, level: int) -> None:
        self._event_log.append({
            "node": node_id,
            "event": event,
            "ts": time.time(),
            "level": level,
        })

    async def execute(self, graph=None) -> dict:
        """Execute the graph.

        Independent nodes of the same topological level run concurrently;
        dependents wait for their level.  The result contains the per-node
        outputs, the failures, and the event log.
        """
        if graph is None:
            graph = nx.DiGraph()
            for node in self._nodes.values():
                graph.add_node(node.id, name=node.name)
            for node in self._nodes.values():
                for dependency in node.depends_on:
                    if dependency in graph:
                        graph.add_edge(dependency, node.id)

        self.reset_event_log()
        results: dict[str, Any] = {}
        errors: dict[str, str] = {}
        shared: dict[str, Any] = {}

        levels = _topological_levels(graph)
        for level_index, level in enumerate(levels):
            runnable = [nid for nid in level if nid in self._nodes]
            if not runnable:
                continue
            gathered = await asyncio.gather(
                *[self._run_node(nid, shared, level_index) for nid in runnable],
                return_exceptions=True,
            )
            for node_id, outcome in zip(runnable, gathered):
                if isinstance(outcome, BaseException):
                    errors[node_id] = f"{type(outcome).__name__}: {outcome}"
                    continue
                results[node_id] = outcome

        return {
            "results": results,
            "errors": errors,
            "event_log": self.event_log(),
            "levels": [list(level) for level in levels],
            "status": "ok" if not errors else "partial",
        }

    async def _run_node(self, node_id: str, shared: dict, level: int) -> Any:
        node = self._nodes[node_id]
        self._record(node_id, "start", level)
        try:
            if node.is_async:
                outcome = await node.fn(shared)
            else:
                outcome = await asyncio.to_thread(node.fn, shared)
        finally:
            self._record(node_id, "end", level)
        if isinstance(outcome, dict):
            shared.update(outcome)
        return outcome

    def execute_sync(self, graph=None) -> dict:
        """Blocking wrapper around :meth:`execute`."""
        return asyncio.run(self.execute(graph))


def _topological_levels(graph) -> list[list[str]]:
    """Group nodes into levels that can run concurrently."""
    if nx is None:  # pragma: no cover
        return [list(graph.nodes())]
    levels: list[list[str]] = []
    remaining = dict(graph.nodes())
    while remaining:
        ready = [
            node for node, data in graph.in_degree()
            if node in remaining and data == 0
        ]
        if not ready:  # cycle: break deterministically
            ready = sorted(remaining)
        for node in ready:
            remaining.pop(node, None)
        levels.append(ready)
        graph = graph.subgraph(remaining)
    return levels
