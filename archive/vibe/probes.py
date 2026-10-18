"""Uncalibrated linear-readout plumbing for user-supplied activation probes.

There are no bundled emotional-state probes. Scores from a linear readout are
not evidence that a user is sarcastic, frustrated, urgent, or confused; a probe
must be trained/derived for the exact model, calibrated, and evaluated before
it is used. The module only provides safe loading, scoring, and optional hooks.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

DEFAULT_PROBE_LAYER = 14
PROBE_NAMES = ("sarcasm", "frustration", "urgency", "confusion")


@dataclass(frozen=True)
class LinearProbe:
    name: str
    weights: tuple[float, ...]
    bias: float = 0.0
    threshold: float = 0.0

    def score(self, activation: Any) -> float:
        if hasattr(activation, "detach"):
            activation = activation.detach().cpu().tolist()
        elif hasattr(activation, "tolist") and not isinstance(activation, (list, tuple)):
            activation = activation.tolist()
        values: list[float] = []

        def visit(item: Any) -> None:
            if isinstance(item, (list, tuple)):
                for child in item:
                    visit(child)
            else:
                values.append(float(item))

        visit(activation)
        if len(values) != len(self.weights):
            raise ValueError("Activation and probe weight dimensions do not match.")
        result = self.bias + sum(x * weight for x, weight in zip(values, self.weights))
        if not math.isfinite(result):
            raise ValueError("Probe score is not finite.")
        return result

    def detected(self, activation: Any) -> bool:
        return self.score(activation) > self.threshold

    def tag(self) -> str:
        return f"[Uncalibrated probe signal: {self.name}; review, do not infer user intent.]"


def load_probes(path: str | Path) -> dict[str, LinearProbe]:
    """Load numeric user-provided probes from JSON; pickle is not used."""
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Probe file must be a JSON object keyed by probe name.")
    probes: dict[str, LinearProbe] = {}
    for name, record in data.items():
        if name not in PROBE_NAMES or not isinstance(record, dict):
            raise ValueError(f"Unsupported probe record {name!r}.")
        weights = record.get("weights")
        if not isinstance(weights, list) or not weights:
            raise ValueError(f"Probe {name!r} must have a non-empty weights array.")
        numeric = tuple(float(value) for value in weights)
        bias = float(record.get("bias", 0.0))
        threshold = float(record.get("threshold", 0.0))
        if not all(math.isfinite(value) for value in (*numeric, bias, threshold)):
            raise ValueError(f"Probe {name!r} contains a non-finite value.")
        probes[name] = LinearProbe(name, numeric, bias, threshold)
    return probes


def register_prefill_probe_hook(
    layer: Any,
    probe: LinearProbe,
    on_detection: Callable[[str, float], None],
) -> Any:
    """Read the mean layer input on a multi-token prefill, without modifying it."""
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("Probe hooks require optional PyTorch.") from exc

    def hook(_module: Any, args: tuple[Any, ...]) -> None:
        if not args or not torch.is_tensor(args[0]):
            return
        hidden = args[0].detach()
        # Treat sequence length > 1 as a prefill heuristic, not a robust signal.
        if hidden.ndim < 3 or hidden.shape[-2] <= 1:
            return
        summary = hidden.float().reshape(-1, hidden.shape[-1]).mean(dim=0).cpu().tolist()
        score = probe.score(summary)
        if score > probe.threshold:
            on_detection(probe.tag(), score)

    return layer.register_forward_pre_hook(hook)
