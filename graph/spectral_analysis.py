"""Compatibility name for the project's repository-topology analyzer.

Despite the requested legacy module name, these operations are SCC detection,
personalized PageRank, and community clustering; they are not eigenvalue-based
spectral graph methods. Prefer importing ``GraphTopologyAnalyzer`` from
``graph.topology``.
"""

from __future__ import annotations

from graph.topology import GraphTopologyAnalyzer


class SpectralAnalyzer(GraphTopologyAnalyzer):
    """Deprecated compatibility alias with the historical specification name."""


__all__ = ["SpectralAnalyzer", "GraphTopologyAnalyzer"]
