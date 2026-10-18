from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ii_state import StateManager


class StateExtensionTests(unittest.TestCase):
    def test_feedback_vector_and_kv_metadata_persist_as_bookkeeping(self):
        with tempfile.TemporaryDirectory() as directory:
            state = StateManager(directory)
            record_id = state.record_negative_outcome(
                "request context", "unsafe_action", "timeout", penalty=2.0
            )
            state.record_task_vector_activation("technical_detailed", 0.4)
            state.record_kv_page("page-000001", "ram_metadata")

            reloaded = StateManager(directory)
            negative = reloaded.get_negative_logit_bias_cache()
            self.assertEqual(negative[-1]["record_id"], record_id)
            self.assertEqual(negative[-1]["penalty"], 2.0)
            self.assertFalse(reloaded.get_task_vector_history()[-1]["applied"])
            self.assertFalse(reloaded.get_kv_page_index()[-1]["transfer_performed"])

    def test_rejects_non_finite_feedback_values(self):
        with tempfile.TemporaryDirectory() as directory:
            state = StateManager(Path(directory))
            with self.assertRaises(ValueError):
                state.record_negative_outcome("state", "action", "error", float("nan"))
            with self.assertRaises(ValueError):
                state.record_task_vector_activation("style", float("inf"))


if __name__ == "__main__":
    unittest.main()
