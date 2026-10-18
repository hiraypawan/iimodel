"""Small, optional algorithm implementations used by the II prototype.

These modules avoid importing heavyweight model frameworks at package import
time. Optional dependencies are loaded only by the feature that needs them.
The experimental steering / merging / KV-paging modules were moved to
``archive/`` in v3.0 because the shipped inference backends cannot use them.
"""

__all__ = []
