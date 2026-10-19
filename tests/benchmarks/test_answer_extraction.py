"""Benchmark: answer extraction is correct on canonical fact sentences.

These cases pin the extraction layer that the ground-truth suite only
exercises over the network.  Three real regressions motivated them:

* name patterns compiled without case-insensitive roles, so "CEO" never
  matched ("Satya Nadella is the CEO of Microsoft." extracted ``None``);
* value patterns looked only at the side of the sentence *after* "is", so
  appositive sentences returned the predicate ("one of the world's great
  cultural centres") and decimals were truncated ("1.4 billion" -> "1");
* both bugs stayed invisible because the only tests that would catch them
  skip offline.

Everything here is deterministic and needs no network.
"""

from __future__ import annotations

import unittest

from reasoning.logic_tree_solver import _extract_name, _extract_value

#: (passage, entity_text, expected name)
NAME_CASES = [
    (
        "Satya Nadella is the CEO of Microsoft.",
        "Microsoft",
        "Satya Nadella",
    ),
    (
        "Mark Zuckerberg, born May 14, 1984, is the chief executive officer of Meta",
        "Meta",
        "Mark Zuckerberg",
    ),
    (
        "The CEO of Meta Platforms is Mark Zuckerberg.",
        "Meta Platforms",
        "Mark Zuckerberg",
    ),
    (
        "Mark Zuckerberg is an American businessman and the chief "
        "executive officer of Meta Platforms.",
        "Meta Platforms",
        "Mark Zuckerberg",
    ),
    (
        "Meta Platforms is led by Mark Zuckerberg.",
        "Meta Platforms",
        "Mark Zuckerberg",
    ),
    (
        "WhatsApp was created by Brian Acton and Jan Koum in 2009.",
        "WhatsApp",
        "Brian Acton",
    ),
]

#: (passage, entity_text) -- must extract nothing (fail closed)
NAME_REJECT_CASES = [
    ("The mitochondrion is the powerhouse of the cell.", "the workshop"),
    ("The institute was created by a research group in 1998.", "the company"),
    ("Satya Nadella is the CEO of Microsoft.", "Satya Nadella"),
]

#: (passage, expected value)
VALUE_CASES = [
    ("Paris is the capital and most populous city of France.", "Paris"),
    (
        "Paris, the capital of France, is one of the world's great cultural centres.",
        "Paris",
    ),
    ("The capital of France is Paris.", "Paris"),
    ("The population of India is 1.4 billion.", "1.4 billion"),
    ("France has a population of 67 million.", "67 million"),
    ("France's capital is Paris.", "Paris"),
    ("The price is $4.99.", "$4.99"),
]

#: passages that must yield no value (fail closed, never the wrong side)
VALUE_REJECT_CASES = [
    "The capital of France is one of the world's great cultural centres.",
    "The population of India is huge.",
]


class NameExtractionTests(unittest.TestCase):
    def test_canonical_name_sentences(self):
        for passage, entity, expected in NAME_CASES:
            with self.subTest(passage=passage):
                self.assertEqual(_extract_name(passage, entity), expected)

    def test_name_extraction_fails_closed(self):
        for passage, entity in NAME_REJECT_CASES:
            with self.subTest(passage=passage):
                self.assertIsNone(_extract_name(passage, entity))


class ValueExtractionTests(unittest.TestCase):
    def test_canonical_value_sentences(self):
        for passage, expected in VALUE_CASES:
            with self.subTest(passage=passage):
                self.assertEqual(_extract_value(passage), expected)

    def test_value_extraction_never_returns_the_predicate(self):
        for passage in VALUE_REJECT_CASES:
            with self.subTest(passage=passage):
                self.assertIsNone(_extract_value(passage))


if __name__ == "__main__":
    unittest.main(verbosity=2)
