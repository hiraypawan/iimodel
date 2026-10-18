from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import networkx as nx

from graph.topology import GraphTopologyAnalyzer
from ii_graph import CodebaseKnowledgeGraph


class GraphTopologyTests(unittest.TestCase):
    def setUp(self):
        self.graph = nx.DiGraph()
        self.graph.add_node("a.py", type="file")
        self.graph.add_node("b.py", type="file")
        self.graph.add_node(
            "a.py::alpha@1", type="FunctionDef", name="alpha",
            qualified_name="alpha", file="a.py"
        )
        self.graph.add_node(
            "b.py::beta@1", type="FunctionDef", name="beta",
            qualified_name="beta", file="b.py"
        )
        self.graph.add_edge("a.py", "a.py::alpha@1", relation="defines")
        self.graph.add_edge("b.py", "b.py::beta@1", relation="defines")

    def test_detects_multi_node_cycle(self):
        self.graph.add_edge("a.py::alpha@1", "b.py::beta@1", relation="calls_resolved")
        self.graph.add_edge("b.py::beta@1", "a.py::alpha@1", relation="calls_resolved")
        cycles = GraphTopologyAnalyzer(self.graph).find_cycles()
        self.assertEqual(cycles, [["a.py::alpha@1", "b.py::beta@1"]])

    def test_finds_a_30_file_python_import_cycle_in_source_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file_count = 30
            for index in range(file_count):
                next_index = (index + 1) % file_count
                (root / f"module_{index:02d}.py").write_text(
                    f"import module_{next_index:02d}\n", encoding="utf-8"
                )
            indexer = CodebaseKnowledgeGraph(str(root))
            self.assertEqual(indexer.build_graph(), file_count)
            cycles = GraphTopologyAnalyzer(indexer.graph).find_cycles()
        self.assertEqual(len(cycles), 1)
        self.assertEqual(len(cycles[0]), file_count)
        self.assertIn("module_00.py", cycles[0])

    def test_rank_uses_indexed_symbol_and_rejects_unknown(self):
        self.graph.add_edge("a.py::alpha@1", "b.py::beta@1", relation="calls_resolved")
        analyzer = GraphTopologyAnalyzer(self.graph)
        self.assertEqual(analyzer.rank_critical_symbols("alpha", top_k=1), ["b.py::beta@1"])
        with self.assertRaises(ValueError):
            analyzer.rank_critical_symbols("missing")

    def test_file_clusters_and_bounded_architecture_map(self):
        self.graph.add_edge("a.py::alpha@1", "b.py::beta@1", relation="calls_resolved")
        analyzer = GraphTopologyAnalyzer(self.graph)
        clusters = analyzer.cluster_subsystems()
        self.assertEqual(len(clusters), 1)
        self.assertEqual(next(iter(clusters.values())), ["a.py", "b.py"])
        summary = analyzer.generate_architecture_map(max_tokens=30)
        self.assertIn("APPROXIMATE", summary)
        self.assertLessEqual(len(summary), 120)


if __name__ == "__main__":
    unittest.main(verbosity=2)
