# SPDX-License-Identifier: MIT
# Algorithm reference: TIES/DARE; mergekit is LGPL-3.0 and no mergekit code is copied.
"""Pure array operations for task-vector sparsification and TIES merging.

This module does not load, edit, or save model weights. It accepts numeric
NumPy-compatible arrays and always creates new results. The implementation is
original II code based on the published TIES/DARE ideas; see
``docs/VENDOR_CREDITS.md`` for references and license notices.
"""

from __future__ import annotations

from typing import Iterable, Sequence


def _numpy():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("Task-vector operations require NumPy.") from exc
    return np


def task_vector(finetuned: object, base: object):
    """Return ``finetuned - base`` as a new array; inputs are not mutated."""
    np = _numpy()
    tuned = np.asarray(finetuned)
    origin = np.asarray(base)
    if tuned.shape != origin.shape:
        raise ValueError("Fine-tuned and base arrays must have the same shape.")
    return np.array(tuned - origin, copy=True)


def combine_task_vectors(vectors: Sequence[object], weights: Sequence[float] | None = None):
    """Weighted task arithmetic sum, returned as a new NumPy array."""
    np = _numpy()
    if not vectors:
        raise ValueError("At least one task vector is required.")
    arrays = [np.asarray(vector) for vector in vectors]
    shape = arrays[0].shape
    if any(array.shape != shape for array in arrays):
        raise ValueError("All task vectors must have the same shape.")
    if weights is None:
        weights = [1.0] * len(arrays)
    if len(weights) != len(arrays):
        raise ValueError("weights must have one value per task vector.")
    if any(not np.isfinite(float(weight)) for weight in weights):
        raise ValueError("weights must be finite.")
    result = np.zeros(shape, dtype=np.result_type(*[a.dtype for a in arrays], np.float32))
    for array, weight in zip(arrays, weights):
        result += array * float(weight)
    return result


def dare_sparsify(
    vectors: Iterable[object],
    drop_rate: float = 0.9,
    *,
    seed: int | None = None,
):
    """Apply seeded DARE-style drop-and-rescale masks to each task vector."""
    np = _numpy()
    if not 0.0 <= drop_rate < 1.0:
        raise ValueError("drop_rate must satisfy 0 <= drop_rate < 1.")
    keep_probability = 1.0 - drop_rate
    rng = np.random.default_rng(seed)
    output = []
    for vector in vectors:
        array = np.asarray(vector)
        mask = rng.random(array.shape) < keep_probability
        output.append(np.array(array * mask / keep_probability, copy=True))
    return output


def trim_by_magnitude(vector: object, density: float = 0.2):
    """Keep the largest-magnitude ``density`` fraction of entries per array."""
    np = _numpy()
    if not 0.0 < density <= 1.0:
        raise ValueError("density must satisfy 0 < density <= 1.")
    array = np.asarray(vector)
    flat = array.reshape(-1)
    if flat.size == 0:
        return np.array(array, copy=True)
    keep_count = max(1, int(np.ceil(density * flat.size)))
    # Stable sorting gives deterministic tie handling across repeated runs.
    order = np.argsort(-np.abs(flat), kind="stable")
    mask = np.zeros(flat.shape, dtype=bool)
    mask[order[:keep_count]] = True
    trimmed = np.where(mask, flat, 0)
    return np.array(trimmed.reshape(array.shape), copy=True)


def ties_merge(task_vectors: Sequence[object], density: float = 0.2):
    """Trim, elect a per-coordinate sign, and average sign-aligned updates.

    Sign ties elect zero and therefore contribute no update. The return value
    is a merged task vector, not a merged model or modified parameter set.
    """
    np = _numpy()
    if not task_vectors:
        raise ValueError("At least one task vector is required.")
    arrays = [np.asarray(vector) for vector in task_vectors]
    shape = arrays[0].shape
    if any(array.shape != shape for array in arrays):
        raise ValueError("All task vectors must have the same shape.")
    trimmed = [trim_by_magnitude(array, density).reshape(-1) for array in arrays]
    stacked = np.stack(trimmed, axis=0)
    elected = np.sign(np.sum(stacked, axis=0))
    aligned = (np.sign(stacked) == elected[None, :]) & (elected[None, :] != 0)
    selected = np.where(aligned, stacked, 0.0)
    counts = np.sum(aligned, axis=0)
    merged = np.divide(
        np.sum(selected, axis=0),
        counts,
        out=np.zeros(shape=(stacked.shape[1],), dtype=np.result_type(stacked, np.float32)),
        where=counts != 0,
    )
    return np.array(merged.reshape(shape), copy=True)


def apply_task_vector(base: object, merged_delta: object, scale: float = 1.0):
    """Return ``base + scale * delta`` without modifying either input."""
    np = _numpy()
    origin = np.asarray(base)
    delta = np.asarray(merged_delta)
    if origin.shape != delta.shape:
        raise ValueError("Base and merged task vector must have the same shape.")
    if not np.isfinite(float(scale)):
        raise ValueError("scale must be finite.")
    return np.array(origin + float(scale) * delta, copy=True)
