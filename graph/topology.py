"""Approximate topology summaries for the repository symbol graph.

SCCs, PageRank, and communities are navigation aids over an approximate graph;
they do not establish semantic importance or architectural truth.
"""

from __future__ import annotations

import networkx as nx


class GraphTopologyAnalyzer:
    """Summarize cycles, centrality, and file-level communities."""

    def __init__(self, graph: nx.DiGraph):
        self.graph = graph

    def find_cycles(self) -> list[list[str]]:
        """Return non-trivial strongly connected components (and self-loops)."""
        cycles = []
        for component in nx.strongly_connected_components(self.graph):
            if len(component) > 1:
                cycles.append(sorted(component))
                continue
            node = next(iter(component))
            if self.graph.has_edge(node, node):
                cycles.append([node])
        return sorted(cycles, key=lambda nodes: (nodes[0], len(nodes)))

    def rank_critical_symbols(self, target: str, top_k: int = 10) -> list[str]:
        """Rank indexed symbols reachable from a target using personalized PageRank.

        ``target`` may be a graph node ID, simple symbol name, or qualified name.
        If a simple name is ambiguous, the first lexicographic node is used; this
        ranking is a heuristic, not proof that a symbol is important.
        """
        if top_k <= 0:
            return []
        target_node = self._resolve_symbol(target)
        if target_node is None:
            raise ValueError(f"No indexed symbol matches {target!r}.")

        scores = self._personalized_pagerank(target_node)
        candidates = [
            node for node, data in self.graph.nodes(data=True)
            if data.get("name") and node != target_node and scores.get(node, 0.0) > 0.0
        ]
        ranked = sorted(candidates, key=lambda node: (-scores.get(node, 0.0), node))
        return ranked[:top_k]

    def cluster_subsystems(self) -> dict[str, list[str]]:
        """Cluster files using resolved call and import edges.

        If the graph has no cross-file links, each file is returned as its own
        cluster. Louvain is used when available; NetworkX's greedy modularity
        algorithm is a fallback. A cluster is a structural hint, not a semantic
        subsystem label.
        """
        file_nodes = sorted(
            node for node, data in self.graph.nodes(data=True)
            if data.get("type") == "file"
        )
        file_graph = nx.Graph()
        file_graph.add_nodes_from(file_nodes)

        for source, target, data in self.graph.edges(data=True):
            if data.get("relation") not in {"calls_resolved", "imports_resolved"}:
                continue
            source_data = self.graph.nodes[source]
            target_data = self.graph.nodes[target]
            source_file = (
                source if source_data.get("type") == "file"
                else source_data.get("file")
            )
            target_file = (
                target if target_data.get("type") == "file"
                else target_data.get("file")
            )
            if source_file in file_graph and target_file in file_graph and source_file != target_file:
                if file_graph.has_edge(source_file, target_file):
                    file_graph[source_file][target_file]["weight"] += 1
                else:
                    file_graph.add_edge(source_file, target_file, weight=1)

        if not file_nodes:
            return {}
        if file_graph.number_of_edges() == 0:
            groups = [{node} for node in file_nodes]
        elif hasattr(nx.algorithms.community, "louvain_communities"):
            groups = nx.algorithms.community.louvain_communities(
                file_graph, weight="weight", seed=0
            )
        else:  # pragma: no cover - for older supported NetworkX releases
            groups = nx.algorithms.community.greedy_modularity_communities(
                file_graph, weight="weight"
            )

        ordered = sorted((sorted(group) for group in groups), key=lambda group: group[0])
        return {f"cluster_{index:02d}": group for index, group in enumerate(ordered, 1)}

    def generate_architecture_map(self, max_tokens: int = 600) -> str:
        """Return a bounded, approximate summary of graph topology."""
        file_count = sum(
            data.get("type") == "file" for _, data in self.graph.nodes(data=True)
        )
        symbol_nodes = [
            node for node, data in self.graph.nodes(data=True) if data.get("name")
        ]
        resolved_edges = [
            (source, target)
            for source, target, data in self.graph.edges(data=True)
            if data.get("relation") == "calls_resolved"
        ]
        resolved_imports = sum(
            data.get("relation") == "imports_resolved"
            for _, _, data in self.graph.edges(data=True)
        )
        lines = [
            "APPROXIMATE REPOSITORY TOPOLOGY",
            f"Files: {file_count}; indexed symbols: {len(symbol_nodes)}; "
            f"resolved call links: {len(resolved_edges)}; "
            f"resolved import links: {resolved_imports}.",
        ]

        cycles = self.find_cycles()
        if cycles:
            lines.append("Strongly connected groups (possible cycles):")
            lines.extend("- " + ", ".join(group) for group in cycles[:8])
        else:
            lines.append("No cycles detected in the indexed graph.")

        clusters = self.cluster_subsystems()
        if clusters:
            lines.append("File clusters (structural hints):")
            for name, files in list(clusters.items())[:10]:
                lines.append(f"- {name}: {', '.join(files[:8])}")

        # A rough 4-character token estimate only; this is not tokenizer-based.
        limit = max(1, max_tokens) * 4
        result = "\n".join(lines)
        if len(result) > limit:
            result = result[: max(0, limit - 24)].rstrip() + "\n...[truncated]"
        return result

    def _personalized_pagerank(
        self,
        target_node: str,
        damping: float = 0.85,
        tolerance: float = 1e-10,
        max_iterations: int = 200,
    ) -> dict[str, float]:
        """Power-iteration PageRank without optional SciPy/NumPy dependencies."""
        nodes = list(self.graph.nodes)
        if not nodes:
            return {}
        personalization = {node: 0.0 for node in nodes}
        personalization[target_node] = 1.0
        ranks = dict(personalization)
        out_weights: dict[str, float] = {}
        for node in nodes:
            out_weights[node] = sum(
                float(data.get("weight", 1.0))
                for _, _, data in self.graph.out_edges(node, data=True)
            )

        for _ in range(max_iterations):
            dangling_weight = sum(
                ranks[node] for node in nodes if out_weights[node] == 0.0
            )
            updated = {
                node: (1.0 - damping) * personalization[node]
                + damping * dangling_weight * personalization[node]
                for node in nodes
            }
            for source in nodes:
                total_weight = out_weights[source]
                if total_weight == 0.0:
                    continue
                for _, target, data in self.graph.out_edges(source, data=True):
                    edge_weight = float(data.get("weight", 1.0))
                    if edge_weight > 0:
                        updated[target] += (
                            damping * ranks[source] * edge_weight / total_weight
                        )
            error = sum(abs(updated[node] - ranks[node]) for node in nodes)
            ranks = updated
            if error < tolerance:
                break
        return ranks

    def _resolve_symbol(self, target: str) -> str | None:
        if target in self.graph and self.graph.nodes[target].get("name"):
            return target
        matches = [
            node for node, data in self.graph.nodes(data=True)
            if data.get("name") == target or data.get("qualified_name") == target
        ]
        return sorted(matches)[0] if matches else None
