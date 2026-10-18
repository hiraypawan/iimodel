"""Entropy-triggered branching for model adapters that expose token state.

This is an adapter contract, not an integration with the current GGUF/LM Studio
backends. The current implementations do not expose a portable KV-cache fork
API. The search below is bounded beam-style rollout search, not a validated
accuracy improvement and not a claim that entropy identifies correctness.
"""

from __future__ import annotations

import math
from typing import Any, Sequence


def _as_logits(logits: Any) -> list[float]:
    if hasattr(logits, "detach"):
        logits = logits.detach().cpu().tolist()
    elif hasattr(logits, "tolist") and not isinstance(logits, (list, tuple)):
        logits = logits.tolist()
    while isinstance(logits, (list, tuple)) and len(logits) == 1 and isinstance(logits[0], (list, tuple)):
        logits = logits[0]
    if not isinstance(logits, (list, tuple)) or not logits:
        raise ValueError("logits must be a non-empty one-dimensional sequence.")
    values = [float(value) for value in logits]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("logits must contain only finite values.")
    return values


def _softmax(logits: Sequence[float]) -> list[float]:
    pivot = max(logits)
    exponents = [math.exp(value - pivot) for value in logits]
    total = sum(exponents)
    return [value / total for value in exponents]


class EntropyMCTS:
    """Branch on high-entropy next-token distributions via a small adapter API.

    A compatible ``model`` supplies ``prefill(prompt)``, ``next_logits(state)``,
    ``clone_state(state)``, ``step(state, token_id)``, and ``decode_tokens(ids)``.
    These operations are absent from the shipped HTTP and llama.cpp wrapper
    interfaces, so calls fail explicitly until an adapter is provided.
    """

    def __init__(self, model: Any, entropy_threshold: float = 2.5):
        if entropy_threshold < 0 or not math.isfinite(entropy_threshold):
            raise ValueError("entropy_threshold must be a finite non-negative number.")
        self.model = model
        self.entropy_threshold = float(entropy_threshold)

    @staticmethod
    def calculate_entropy(logits: Any) -> float:
        """Calculate Shannon entropy in nats from raw logits."""
        probabilities = _softmax(_as_logits(logits))
        return -sum(p * math.log(p) for p in probabilities if p > 0)

    def should_branch(self, logits: Any) -> bool:
        return self.calculate_entropy(logits) > self.entropy_threshold

    def _require_adapter(self, *methods: str) -> None:
        missing = [name for name in methods if not callable(getattr(self.model, name, None))]
        if missing:
            raise NotImplementedError(
                "Model adapter lacks token-state methods: " + ", ".join(missing)
            )

    def fork_and_search(
        self,
        kv_cache: Any,
        n_branches: int = 3,
        depth: int = 5,
    ) -> str:
        """Expand top candidates at uncertain steps and return the best rollout."""
        if n_branches < 1 or depth < 1:
            raise ValueError("n_branches and depth must both be positive.")
        self._require_adapter("next_logits", "clone_state", "step", "decode_tokens")
        beam: list[tuple[Any, list[int], float]] = [(kv_cache, [], 0.0)]
        for _ in range(depth):
            expanded: list[tuple[Any, list[int], float]] = []
            for state, token_ids, accumulated_logprob in beam:
                probabilities = _softmax(_as_logits(self.model.next_logits(state)))
                entropy = -sum(p * math.log(p) for p in probabilities if p > 0)
                branch_count = n_branches if entropy > self.entropy_threshold else 1
                token_order = sorted(
                    range(len(probabilities)), key=lambda idx: (-probabilities[idx], idx)
                )[:branch_count]
                for token_id in token_order:
                    next_state = self.model.clone_state(state)
                    stepped = self.model.step(next_state, token_id)
                    if stepped is not None:
                        next_state = stepped
                    probability = max(probabilities[token_id], 1e-300)
                    expanded.append(
                        (next_state, token_ids + [token_id], accumulated_logprob + math.log(probability))
                    )
            if not expanded:
                break
            # Keep a bounded beam. Scores are log-likelihoods, not correctness.
            beam = sorted(expanded, key=lambda item: (-item[2], item[1]))[:n_branches]
        if not beam:
            return ""
        best = max(beam, key=lambda item: (item[2], tuple(-token for token in item[1])))
        return str(self.model.decode_tokens(best[1]))

    def generate_with_search(self, prompt: str, max_new_tokens: int | None = None) -> str:
        """Greedily continue low-entropy tokens and branch at uncertain steps."""
        self._require_adapter(
            "prefill", "next_logits", "clone_state", "step", "decode_tokens"
        )
        state = self.model.prefill(prompt)
        token_ids: list[int] = []
        limit = (
            int(getattr(self.model, "max_new_tokens", 128))
            if max_new_tokens is None
            else int(max_new_tokens)
        )
        if limit < 1:
            raise ValueError("max_new_tokens must be positive.")
        for _ in range(limit):
            logits = _as_logits(self.model.next_logits(state))
            probabilities = _softmax(logits)
            if self.should_branch(logits):
                suffix = self.fork_and_search(state, n_branches=3, depth=min(5, limit - len(token_ids)))
                prefix = str(self.model.decode_tokens(token_ids))
                return prefix + suffix
            token_id = max(range(len(probabilities)), key=lambda idx: (probabilities[idx], -idx))
            token_ids.append(token_id)
            stepped = self.model.step(state, token_id)
            if stepped is not None:
                state = stepped
            stop_ids = set(getattr(self.model, "stop_token_ids", ()))
            if token_id in stop_ids:
                break
        return str(self.model.decode_tokens(token_ids))
