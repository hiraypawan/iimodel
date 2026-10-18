"""Deprecated compatibility alias for the repository-topology analyzer.

The canonical module is :mod:`graph.topology`.  Despite the historical
spelling, these operations are SCC detection, personalised PageRank and
community clustering -- they are **not** eigenvalue-based spectral graph
methods.  Importing this module prints a one-line deprecation warning.
"""

from __future__ import annotations

import sys
import warnings

from graph.topology import GraphTopologyAnalyzer

_DEPRECATION_MESSAGE = (
    "[DEPRECATED] graph.spectral_analysis is an alias for graph.topology; "
    "the analysis is SCC detection, PageRank and community clustering -- not "
    "eigenvalue-based spectral methods."
)

warnings.warn(
    "graph.spectral_analysis is deprecated; use graph.topology instead.",
    DeprecationWarning,
    stacklevel=2,
)
print(_DEPRECATION_MESSAGE, file=sys.stderr)


class SpectralAnalyzer(GraphTopologyAnalyzer):
    """Deprecated compatibility alias with the historical specification name."""


__all__ = ["SpectralAnalyzer", "GraphTopologyAnalyzer"]
