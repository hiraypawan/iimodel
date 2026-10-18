"""Ensure unsupported heavy features are not exposed as live agent tools."""

from __future__ import annotations

import unittest

import core.ii_agent as ii_agent


class AgentToolTests(unittest.TestCase):
    def test_vision_localization_is_not_loaded_or_exposed(self):
        names = {
            tool["function"]["name"]
            for tool in ii_agent.TOOLS
        }
        self.assertNotIn("visual_ground", names)
        self.assertFalse(hasattr(ii_agent, "vision_grounder"))


if __name__ == "__main__":
    unittest.main()
