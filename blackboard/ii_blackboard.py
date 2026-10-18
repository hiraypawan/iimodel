"""Compatibility facade for the root blackboard implementation."""

from ii_blackboard import (
    Blackboard,
    BlackboardArchitect,
    BlackboardBuilder,
    BlackboardEntry,
    BlackboardInspector,
    InterfaceSpec,
    blackboard_tool,
)

__all__ = [
    "Blackboard", "BlackboardArchitect", "BlackboardBuilder", "BlackboardEntry",
    "BlackboardInspector", "InterfaceSpec", "blackboard_tool",
]
