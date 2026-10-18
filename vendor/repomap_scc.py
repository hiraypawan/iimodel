# SPDX-License-Identifier: MIT
# Conceptual reference: Aider-AI/aider (Apache-2.0); no source copied.
"""Repository symbol-map adapter with topology summaries.

This is an original adapter around II's existing AST/tree-sitter indexer. It is
conceptually informed by Aider's repository-map approach, but contains no copied
Aider source. Aider is licensed under Apache-2.0; see docs/VENDOR_CREDITS.md.
The adapter itself is distributed under this repository's MIT license.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from graph.topology import GraphTopologyAnalyzer
from ii_graph import CodebaseKnowledgeGraph


class RepoMapSCC:
    """Build a repository graph and expose navigation-oriented summaries.

    Python files are indexed with ``ast``. JavaScript/TypeScript files use the
    optional tree-sitter grammars, with a regex fallback. Cross-file calls and
    imports are approximate. SCCs, PageRank, and clusters do not establish
    semantic importance or architectural correctness.
    """

    def __init__(self, root_dir: str | Path, *, max_tokens: int = 600):
        self.root_dir = Path(root_dir).resolve()
        self.max_tokens = max(1, int(max_tokens))
        self.indexer = CodebaseKnowledgeGraph(str(self.root_dir))

    @property
    def graph(self):
        return self.indexer.graph

    def build(self) -> int:
        """Index supported source files and resolve unambiguous references."""
        return self.indexer.build_graph()

    def generate_architecture_map(self) -> str:
        """Return a rough character-budgeted map (not a tokenizer measurement)."""
        return GraphTopologyAnalyzer(self.graph).generate_architecture_map(
            max_tokens=self.max_tokens
        )

    def rank_critical_symbols(self, target: str, top_k: int = 10) -> list[str]:
        return GraphTopologyAnalyzer(self.graph).rank_critical_symbols(target, top_k)

    def find_cycles(self) -> list[list[str]]:
        return GraphTopologyAnalyzer(self.graph).find_cycles()

    def cluster_subsystems(self) -> dict[str, list[str]]:
        return GraphTopologyAnalyzer(self.graph).cluster_subsystems()

    def get_repository_index(self, max_tokens: int = 1000) -> str:
        return self.indexer.get_repository_index(max_tokens=max_tokens)

    def find_bridge_path(self, source_symbol: str, target_symbol: str) -> str:
        return self.indexer.find_bridge_path(source_symbol, target_symbol)

    def to_summary(self) -> dict[str, Any]:
        analyzer = GraphTopologyAnalyzer(self.graph)
        return {
            "architecture_map": analyzer.generate_architecture_map(self.max_tokens),
            "cycles": analyzer.find_cycles(),
            "clusters": analyzer.cluster_subsystems(),
            "approximate": True,
        }


# The old project used a “holographic index” name. Keep a narrow alias for
# callers migrating to the more accurate repository-index terminology.
RepoMapSCC.get_holographic_index = RepoMapSCC.get_repository_index  # type: ignore[attr-defined]
