"""Benchmark: the logic-tree decomposition is canonical and deterministic.

For a fixed set of questions the solver must produce the *exact* number of
hops in the *exact* order.  The decomposition is a pure function of the
question text, so this is a real regression test -- not a self-assertion.
"""

from __future__ import annotations

import unittest

from reasoning.logic_tree_solver import (
    MAX_HOPS,
    build_hop_graph,
    decompose,
    topological_order,
)

#: (question, expected hop types in execution order, expected entity texts)
CANONICAL_CASES = [
    (
        "What is the capital of France?",
        ["attribute"],
        ["France"],
    ),
    (
        "What is the population of Paris?",
        ["attribute"],
        ["Paris"],
    ),
    (
        "Who is the CEO of the company that created WhatsApp?",
        ["creator", "leadership"],
        ["WhatsApp", "the company"],
    ),
    (
        "Who founded Tesla and who is the CEO of SpaceX?",
        ["creator", "leadership"],
        ["Tesla", "SpaceX"],
    ),
    (
        "Is the Eiffel Tower taller than the Empire State Building?",
        ["comparison"],
        ["the Eiffel Tower"],
    ),
]


class LogicTreeDecompositionTests(unittest.TestCase):
    def test_canonical_questions_have_exact_hop_counts_and_order(self):
        for question, expected_types, expected_entities in CANONICAL_CASES:
            with self.subTest(question=question):
                hops = decompose(question)
                self.assertEqual(
                    [hop.hop_type for hop in hops], expected_types,
                    "unexpected hop types",
                )
                self.assertEqual(
                    [hop.entity_text for hop in hops], expected_entities,
                    "unexpected entity texts",
                )

    def test_hop_count_never_exceeds_the_cap(self):
        for question, _, _ in CANONICAL_CASES:
            hops = decompose(question)
            self.assertLessEqual(len(hops), MAX_HOPS)

    def test_graph_execution_order_is_topological(self):
        for question, _, _ in CANONICAL_CASES:
            with self.subTest(question=question):
                hops = decompose(question)
                graph = build_hop_graph(hops)
                order = topological_order(graph)
                self.assertEqual(sorted(order), sorted(hop.id for hop in hops))
                position = {node_id: index for index, node_id in enumerate(order)}
                for hop in hops:
                    for dependency in hop.depends_on:
                        self.assertLess(
                            position[dependency], position[hop.id],
                            "a dependency must be executed before its dependent",
                        )

    def test_nested_hop_depends_on_the_inner_hop(self):
        hops = decompose("Who is the CEO of the company that created WhatsApp?")
        self.assertEqual(len(hops), 2)
        self.assertEqual(hops[1].depends_on, [hops[0].id])

    def test_unmatched_question_becomes_a_single_direct_hop(self):
        hops = decompose("zqrxv fluff wibble")
        self.assertEqual(len(hops), 1)
        self.assertEqual(hops[0].hop_type, "direct")

    def test_decomposition_is_deterministic(self):
        question = "Who is the CEO of the company that created WhatsApp?"
        first = [(hop.id, hop.hop_type, hop.entity_text) for hop in decompose(question)]
        second = [(hop.id, hop.hop_type, hop.entity_text) for hop in decompose(question)]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main(verbosity=2)
