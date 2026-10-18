from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from reasoning.entropy_mcts import EntropyMCTS
from vendor.kv_pager import KVCachePager
from vendor.orthogonal_steering import (
    contrastive_vector,
    gram_schmidt,
    mean_vector,
    rescale_to_norm,
    save_vector,
    load_vector,
    vector_norm,
)
from vibe.probes import LinearProbe, PROBE_NAMES
from vibe.steering import STYLE_NAMES, SteeringController


class FakeTokenModel:
    max_new_tokens = 3

    def prefill(self, _prompt):
        return {"position": 0}

    def next_logits(self, state):
        return [0.0, 0.0] if state["position"] < 3 else [1.0, -1.0]

    def clone_state(self, state):
        return dict(state)

    def step(self, state, _token_id):
        state["position"] += 1
        return state

    def decode_tokens(self, token_ids):
        return "".join("a" if token == 0 else "b" for token in token_ids)


class VendorAlgorithmTests(unittest.TestCase):
    def test_contrastive_vectors_orthogonalization_and_norm_rescaling(self):
        direction = contrastive_vector([[2.0, 1.0], [4.0, 1.0]], [[1.0, 1.0]])
        self.assertEqual(direction, [2.0, 0.0])
        basis = gram_schmidt([[1, 0], [1, 1], [2, 0]])
        self.assertEqual(len(basis), 2)
        self.assertAlmostEqual(sum(a * b for a, b in zip(*basis)), 0.0)
        self.assertAlmostEqual(vector_norm(rescale_to_norm([3, 4], 10)), 10.0)

    def test_vector_helpers_reject_non_finite_values(self):
        with self.assertRaises(ValueError):
            mean_vector([[1.0, float("nan")]])
        with self.assertRaises(ValueError):
            gram_schmidt([[1.0, float("inf")]])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                save_vector(Path(directory) / "bad.npy", [float("nan")])

    def test_vector_npy_roundtrip_and_steering_slots_are_not_fabricated(self):
        with tempfile.TemporaryDirectory() as directory:
            vector_path = save_vector(Path(directory) / "warm_empathetic.npy", [1.0, 2.0])
            self.assertEqual(load_vector(vector_path), [1.0, 2.0])
        controller = SteeringController(vector_dir=Path(directory) / "no-vectors")
        self.assertEqual(controller.list_available_vectors(), [])
        self.assertEqual(len(controller.list_supported_styles()), 5)
        self.assertEqual(len(STYLE_NAMES), 5)

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy is optional")
    def test_ties_and_dare_are_pure_array_operations(self):
        import numpy as np
        from vendor.ties_merging import apply_task_vector, dare_sparsify, ties_merge

        first = np.array([1.0, 3.0])
        second = np.array([2.0, -2.0])
        first_copy = first.copy()
        merged = ties_merge([first, second], density=1.0)
        self.assertEqual(merged.tolist(), [1.5, 3.0])
        combined = apply_task_vector(np.array([10.0, 10.0]), merged)
        self.assertEqual(combined.tolist(), [11.5, 13.0])
        self.assertTrue(np.array_equal(first, first_copy))
        self.assertTrue(np.array_equal(dare_sparsify([first], 0.5, seed=4)[0], dare_sparsify([first], 0.5, seed=4)[0]))

    def test_kv_pager_indexes_64_token_boxes_but_does_not_page_payload(self):
        pager = KVCachePager()
        vectors = [[1.0, 0.0] for _ in range(64)] + [[-1.0, 0.0]]
        pages = pager.index_key_vectors(vectors)
        self.assertEqual([(page.token_start, page.token_end) for page in pages], [(0, 64), (64, 65)])
        self.assertEqual(pager.retrieve([1.0, 0.0], top_k=1)[0].page_id, "page-000000")
        with self.assertRaises(NotImplementedError):
            pager.offload_page("page-000000", object())

    def test_entropy_monitor_branches_only_with_adapter_and_probe_is_raw_readout(self):
        low = EntropyMCTS(FakeTokenModel(), entropy_threshold=1.0)
        high = EntropyMCTS(FakeTokenModel(), entropy_threshold=0.1)
        self.assertLess(low.calculate_entropy([5.0, 0.0]), 1.0)
        self.assertTrue(high.should_branch([0.0, 0.0]))
        self.assertTrue(high.generate_with_search("prompt"))
        with self.assertRaises(ValueError):
            high.generate_with_search("prompt", max_new_tokens=0)
        probe = LinearProbe("frustration", (1.0, -1.0), threshold=0.5)
        self.assertTrue(probe.detected([1.0, 0.0]))
        self.assertIn("Uncalibrated", probe.tag())
        self.assertEqual(len(PROBE_NAMES), 4)

    def test_no_layer_adapter_means_search_is_not_supported(self):
        search = EntropyMCTS(object())
        with self.assertRaises(NotImplementedError):
            search.generate_with_search("prompt")


if __name__ == "__main__":
    unittest.main()
