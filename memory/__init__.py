"""Disk-backed paged memory for II (the Exocortex).

The Exocortex stores only what the user explicitly approved, keeps every
record on disk, and can show or delete what it holds on request.
"""

from .exocortex import (
    Exocortex,
    ProfileItem,
    propose_profile_items,
    NEURAL_EMBEDDING_FUTURE_WORK,
)

__all__ = [
    "Exocortex",
    "ProfileItem",
    "propose_profile_items",
    "NEURAL_EMBEDDING_FUTURE_WORK",
]
