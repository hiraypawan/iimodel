"""Opt-in tone-vector loading and hooks for compatible PyTorch models.

No precomputed vector files are included because valid vectors must be extracted
from a specific model/checkpoint and evaluated. The listed style names are slots,
not claims that a corresponding vector exists or works.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from archive.vendor.orthogonal_steering import load_vector, register_steering_pre_hook, save_vector

STYLE_NAMES = (
    "warm_empathetic",
    "professional_precise",
    "casual_friendly",
    "technical_detailed",
    "concise_direct",
)


class SteeringController:
    def __init__(
        self,
        vector_dir: str | Path | None = None,
        target_layer: Any | None = None,
        state_manager: Any | None = None,
    ):
        self.vector_dir = Path(vector_dir) if vector_dir else Path(__file__).with_name("vectors")
        self.target_layer = target_layer
        self.state_manager = state_manager
        self._handles: list[Any] = []

    def list_available_vectors(self) -> list[str]:
        """Return styles with real .npy files on disk; normally empty by default."""
        if not self.vector_dir.exists():
            return []
        return sorted(path.stem for path in self.vector_dir.glob("*.npy") if path.stem in STYLE_NAMES)

    def list_supported_styles(self) -> list[str]:
        """Return the five proposed names, not vector availability or efficacy."""
        return list(STYLE_NAMES)

    def save_vector(self, name: str, vector: Any) -> Path:
        if name not in STYLE_NAMES:
            raise ValueError(f"Unknown style vector name: {name!r}.")
        return save_vector(self.vector_dir / f"{name}.npy", vector)

    def apply_vector(
        self,
        name: str,
        coefficient: float = 1.0,
        *,
        layer: Any | None = None,
        rescale_norm: bool = True,
    ) -> Any:
        if name not in STYLE_NAMES:
            raise ValueError(f"Unknown style vector name: {name!r}.")
        vector_path = self.vector_dir / f"{name}.npy"
        if not vector_path.is_file():
            raise FileNotFoundError(
                f"No validated vector for {name!r}; extract and review one before use."
            )
        target = layer if layer is not None else self.target_layer
        if target is None:
            raise ValueError("A compatible PyTorch layer module must be supplied.")
        handle = register_steering_pre_hook(
            target,
            load_vector(vector_path),
            coefficient=coefficient,
            rescale_norm=rescale_norm,
        )
        self._handles.append(handle)
        if self.state_manager is not None:
            self.state_manager.record_task_vector_activation(name, coefficient, applied=True)
        return handle

    def remove_hooks(self) -> None:
        for handle in self._handles:
            handle.remove()
        self._handles.clear()
