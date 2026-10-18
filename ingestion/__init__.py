"""Repository ingestion and conservative license screening utilities.

This package owns everything that touches the network *for reading*:
:mod:`ingestion.ii_web` (search + fetch + evidence) and
:mod:`ingestion.repo_ingestor` (license-gated GitHub ingestion).

Backwards-compatible import alias
---------------------------------
Older releases exposed ``ii_web`` as a top-level module.  The canonical
location is :mod:`ingestion.ii_web`; the historical name is registered in
``sys.modules`` here so ``import ii_web`` keeps working for code that
imports this package first.
"""

from __future__ import annotations

import sys

from . import ii_web as _ii_web

# Deprecated top-level alias (``import ii_web`` still resolves).
sys.modules.setdefault("ii_web", _ii_web)

__all__ = ["ii_web"]
