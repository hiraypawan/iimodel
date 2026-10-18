# SPDX-License-Identifier: MIT
# Conceptual reference: vgel/repeng (MIT); no source copied.
"""Optional, zero-weight-update activation-vector utilities.

The functions implement common contrastive-vector and Gram-Schmidt operations
without importing PyTorch or NumPy at module import time. The activation-hook
adapter is experimental and works only with compatible PyTorch modules. No
vectors are bundled: users must provide labeled activations and validate any
resulting behavior. Conceptual references and licenses are documented in
``docs/VENDOR_CREDITS.md``; this file contains original II code.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

DEFAULT_LAYERS = tuple(range(14, 19))


def _as_flat(values: Any) -> list[float]:
    """Convert nested Python/array/tensor values to a flat list of floats."""
    if hasattr(values, "detach"):
        values = values.detach().cpu().tolist()
    elif hasattr(values, "tolist") and not isinstance(values, (list, tuple)):
        values = values.tolist()

    flat: list[float] = []

    def visit(value: Any) -> None:
        if isinstance(value, (list, tuple)):
            for item in value:
                visit(item)
        else:
            flat.append(float(value))

    visit(values)
    if not flat:
        raise ValueError("Activation vectors must contain at least one number.")
    if any(not math.isfinite(value) for value in flat):
        raise ValueError("Activation vectors must contain only finite numbers.")
    return flat


def vector_norm(vector: Sequence[float]) -> float:
    values = [float(value) for value in vector]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Vectors must contain only finite numbers.")
    return math.sqrt(sum(value**2 for value in values))


def mean_vector(samples: Iterable[Any]) -> list[float]:
    """Average activation samples after flattening them to one dimension."""
    rows = [_as_flat(sample) for sample in samples]
    if not rows:
        raise ValueError("At least one activation sample is required.")
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise ValueError("All activation samples must have the same flattened size.")
    return [sum(row[index] for row in rows) / len(rows) for index in range(width)]


def contrastive_vector(positive: Iterable[Any], negative: Iterable[Any]) -> list[float]:
    """Return mean(positive activations) minus mean(negative activations)."""
    pos = mean_vector(positive)
    neg = mean_vector(negative)
    if len(pos) != len(neg):
        raise ValueError("Positive and negative activations must have matching sizes.")
    return [left - right for left, right in zip(pos, neg)]


def gram_schmidt(vectors: Iterable[Any], tolerance: float = 1e-12) -> list[list[float]]:
    """Return an orthonormal basis for the non-degenerate input directions."""
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative.")
    basis: list[list[float]] = []
    for raw in vectors:
        candidate = _as_flat(raw)
        if basis and any(len(candidate) != len(item) for item in basis):
            raise ValueError("All vectors must have the same flattened size.")
        for unit in basis:
            projection = sum(x * y for x, y in zip(candidate, unit))
            candidate = [x - projection * y for x, y in zip(candidate, unit)]
        magnitude = vector_norm(candidate)
        if magnitude <= tolerance:
            continue
        basis.append([value / magnitude for value in candidate])
    return basis


def rescale_to_norm(vector: Any, baseline_norm: float) -> list[float]:
    """Rescale a vector to a requested norm without changing its direction."""
    if baseline_norm < 0 or not math.isfinite(baseline_norm):
        raise ValueError("baseline_norm must be a finite non-negative number.")
    values = _as_flat(vector)
    magnitude = vector_norm(values)
    if magnitude == 0:
        return values
    scale = baseline_norm / magnitude
    return [value * scale for value in values]


def save_vector(path: str | Path, vector: Any) -> Path:
    """Save a flat float32 vector as ``.npy``; NumPy is an optional dependency."""
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("Saving .npy steering vectors requires NumPy.") from exc
    output = Path(path)
    if output.suffix.lower() != ".npy":
        raise ValueError("Steering vectors must use the .npy extension.")
    output.parent.mkdir(parents=True, exist_ok=True)
    np.save(output, np.asarray(_as_flat(vector), dtype=np.float32), allow_pickle=False)
    return output


def load_vector(path: str | Path) -> list[float]:
    """Load a numeric ``.npy`` vector with pickle loading disabled."""
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("Loading .npy steering vectors requires NumPy.") from exc
    source = Path(path)
    if source.suffix.lower() != ".npy":
        raise ValueError("Steering vectors must use the .npy extension.")
    values = np.load(source, allow_pickle=False)
    if values.dtype.kind not in "fiu" or values.ndim < 1:
        raise ValueError("Steering vector must be a numeric array.")
    return _as_flat(values)


def extract_layer_mean(
    model: Any,
    layer: Any,
    examples: Iterable[Any],
    forward_example: Callable[[Any, Any], Any],
) -> list[float]:
    """Capture mean pre-layer activations for examples using a forward pre-hook.

    ``forward_example`` is responsible for constructing model inputs. This
    adapter never calls a tokenizer, never downloads a model, and restores the
    model's prior training/evaluation mode after capture.
    """
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("Activation extraction requires optional PyTorch.") from exc

    captured: list[list[float]] = []

    def capture(_module: Any, args: tuple[Any, ...]) -> None:
        if not args or not torch.is_tensor(args[0]):
            return
        value = args[0].detach().float()
        if value.ndim == 0:
            return
        if value.ndim > 1:
            value = value.reshape(-1, value.shape[-1]).mean(dim=0)
        captured.append(value.cpu().tolist())

    handle = layer.register_forward_pre_hook(capture)
    was_training = getattr(model, "training", None)
    try:
        if hasattr(model, "eval"):
            model.eval()
        with torch.no_grad():
            for example in examples:
                forward_example(model, example)
    finally:
        handle.remove()
        if was_training is True and hasattr(model, "train"):
            model.train()
    if not captured:
        raise ValueError("No compatible layer-input activations were captured.")
    return mean_vector(captured)


def register_steering_pre_hook(
    layer: Any,
    vector: Any,
    coefficient: float = 1.0,
    *,
    rescale_norm: bool = True,
) -> Any:
    """Register an experimental PyTorch layer-input steering hook.

    The hook adds a supplied vector to the first tensor input and optionally
    restores each token's original L2 norm. It does not change model weights.
    The returned PyTorch hook handle must be removed when steering is disabled.
    """
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("Activation steering requires optional PyTorch.") from exc
    if not math.isfinite(coefficient):
        raise ValueError("coefficient must be finite.")
    direction = _as_flat(vector)

    def hook(_module: Any, args: tuple[Any, ...]) -> tuple[Any, ...]:
        if not args or not torch.is_tensor(args[0]):
            raise TypeError("Target layer's first input must be a tensor.")
        hidden = args[0]
        if hidden.shape[-1] != len(direction):
            raise ValueError(
                f"Vector width {len(direction)} does not match hidden width {hidden.shape[-1]}."
            )
        vector_tensor = torch.as_tensor(direction, dtype=hidden.dtype, device=hidden.device)
        adjusted = hidden + coefficient * vector_tensor
        if rescale_norm:
            original_norm = torch.linalg.vector_norm(hidden, dim=-1, keepdim=True)
            adjusted_norm = torch.linalg.vector_norm(adjusted, dim=-1, keepdim=True)
            safe_norm = torch.clamp(adjusted_norm, min=torch.finfo(adjusted.dtype).eps)
            adjusted = adjusted * (original_norm / safe_norm)
        return (adjusted, *args[1:])

    return layer.register_forward_pre_hook(hook)
