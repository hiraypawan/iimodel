"""Graph-analysis helpers for the II prototype.

The canonical module is :mod:`graph.topology`.  ``SpectralAnalyzer`` is a
deprecated alias; touching it prints a one-line warning.
"""

from __future__ import annotations

from .topology import GraphTopologyAnalyzer

__all__ = ["GraphTopologyAnalyzer", "SpectralAnalyzer"]


def __getattr__(name: str):
    """Lazily expose the deprecated alias (and its warning)."""
    if name == "SpectralAnalyzer":
        from . import spectral_analysis

        return spectral_analysis.SpectralAnalyzer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
