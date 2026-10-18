"""Compatibility facade for the root local-model adapter."""

from llama_local import DummyModel, LMStudioModel, LocalModel

__all__ = ["DummyModel", "LMStudioModel", "LocalModel"]
