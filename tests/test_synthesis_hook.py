from __future__ import annotations

import unittest

from reasoning.ii_synthesis import EnumerativeSynthesizer


class SynthesisHookTests(unittest.TestCase):
    def test_optional_verifier_can_reject_a_example_matching_candidate(self):
        seen = []

        def reject(code):
            seen.append(code)
            return False, "fixture verifier rejected candidate"

        result = EnumerativeSynthesizer().synthesize(
            [([1, 2], 3), ([5, 7], 12)],
            max_lines=1,
            verification_hook=reject,
        )
        self.assertFalse(result.found)
        self.assertIn("add(x0, x1)", seen)

    def test_default_synthesis_remains_bounded_without_hook(self):
        result = EnumerativeSynthesizer().synthesize([([1, 2], 3)], max_lines=1)
        self.assertTrue(result.found)
        self.assertNotIn("verification hook", result.detail)


if __name__ == "__main__":
    unittest.main()
