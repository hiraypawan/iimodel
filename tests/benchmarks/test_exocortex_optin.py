"""Benchmark: the Exocortex never writes anything without explicit consent.

The rules under test:

* before consent, ``remember()`` writes **nothing**,
* after consent, approved facts are stored and visible,
* revoked consent deletes the stored facts,
* a proposed profile item is only stored after it is approved explicitly,
* the "what I remember" view always shows exactly what is stored.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from memory.exocortex import Exocortex, propose_profile_items


class ExocortexOptInTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.exocortex = Exocortex(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_write_before_consent(self):
        self.assertEqual(self.exocortex.consent_state(), "unknown")
        written = self.exocortex.remember("preference:language", "Hinglish")
        self.assertFalse(written, "remember() must refuse without consent")
        self.assertEqual(self.exocortex.what_i_remember(), [])
        self.assertEqual(self.exocortex.fact_count if hasattr(self.exocortex, "fact_count") else 0, 0)

    def test_context_is_empty_before_consent(self):
        self.exocortex.remember("k", "v")
        self.assertEqual(self.exocortex.get_user_context(), {})

    def test_write_after_consent_and_visible_in_the_view(self):
        self.exocortex.set_consent(True)
        self.assertTrue(self.exocortex.remember("preference:language", "Hinglish"))
        facts = self.exocortex.what_i_remember()
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["key"], "preference:language")
        self.assertEqual(facts[0]["value"], "Hinglish")
        self.assertEqual(
            self.exocortex.get_user_context(), {"preference:language": "Hinglish"}
        )

    def test_revoking_consent_deletes_the_facts(self):
        self.exocortex.set_consent(True)
        self.exocortex.remember("k", "v")
        self.exocortex.set_consent(False)
        self.assertEqual(self.exocortex.what_i_remember(), [])
        self.assertEqual(self.exocortex.get_user_context(), {})

    def test_forget_removes_a_single_fact(self):
        self.exocortex.set_consent(True)
        self.exocortex.remember("a", "1")
        self.exocortex.remember("b", "2")
        self.assertTrue(self.exocortex.forget("a"))
        self.assertFalse(self.exocortex.forget("a"))
        remaining = [fact["key"] for fact in self.exocortex.what_i_remember()]
        self.assertEqual(remaining, ["b"])

    def test_profile_proposals_are_not_stored_without_approval(self):
        proposals = propose_profile_items("I prefer Python and my role is engineer")
        self.assertTrue(proposals)
        self.assertEqual(self.exocortex.what_i_remember(), [])
        self.exocortex.set_consent(True)
        self.assertTrue(self.exocortex.approve_profile_item(proposals[0]))
        self.assertEqual(len(self.exocortex.what_i_remember()), 1)

    def test_profiling_is_never_silent(self):
        description = self.exocortex.describe()
        self.assertTrue(description["profiling"]["opt_in"])
        self.assertFalse(description["profiling"]["silent_profiling"])
        self.assertIn("Exocortex", description["name"])
        self.assertNotIn("Infinite", description["name"])

    def test_recall_uses_keyword_vectors(self):
        self.exocortex.set_consent(True)
        self.exocortex.remember("preference:language", "Hinglish")
        self.exocortex.remember("preference:editor", "Neovim")
        recalled = self.exocortex.recall("which editor do I use")
        self.assertTrue(recalled)
        self.assertEqual(recalled[0]["key"], "preference:editor")

    def test_page_cache_is_lru_bounded(self):
        exocortex = Exocortex(Path(self._tmp.name), page_cache_size=2)
        exocortex.cache_page("https://a", "a")
        exocortex.cache_page("https://b", "b")
        exocortex.cache_page("https://c", "c")
        self.assertIsNone(exocortex.cached_page("https://a"))
        self.assertEqual(exocortex.cached_page("https://c"), "c")

    def test_episodes_and_tool_runs_are_recorded(self):
        episode = self.exocortex.start_episode("test episode")
        self.exocortex.end_episode(episode, "done")
        self.exocortex.record_tool_run("search_web", True, 120)
        self.assertTrue(self.exocortex.episodes())
        self.assertTrue(self.exocortex.tool_runs())


if __name__ == "__main__":
    unittest.main(verbosity=2)
