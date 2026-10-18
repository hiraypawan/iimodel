"""Focused tests for the repository symbol indexer."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graph.ii_graph import CodebaseKnowledgeGraph, HAS_TREE_SITTER


class CodebaseGraphTests(unittest.TestCase):
    def test_python_symbols_calls_and_repeat_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lib.py").write_text(
                "def add(left, right):\n    return left + right\n",
                encoding="utf-8",
            )
            (root / "app.py").write_text(
                "from lib import add\n\ndef total(a, b):\n    return add(a, b)\n",
                encoding="utf-8",
            )

            graph = CodebaseKnowledgeGraph(str(root))
            self.assertEqual(graph.build_graph(), 2)
            first_node_count = graph.graph.number_of_nodes()
            self.assertEqual(graph.build_graph(), 2)
            self.assertEqual(graph.graph.number_of_nodes(), first_node_count)

            symbol = graph.get_symbol("total")
            self.assertIsNotNone(symbol)
            self.assertEqual(symbol.file, "app.py")
            self.assertEqual(symbol.lineno, 3)
            self.assertTrue(
                any(
                    data.get("relation") == "calls_resolved"
                    for _, _, data in graph.graph.edges(data=True)
                )
            )
            index = graph.get_repository_index()
            self.assertIn("app.py", index)
            self.assertIn("total(a, b)", index)

    def test_python_qualified_names_keep_same_named_methods_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scopes.py").write_text(
                "class First:\n"
                "    def run(self):\n        return 1\n\n"
                "class Second:\n"
                "    def run(self):\n        return 2\n\n"
                "def outer():\n"
                "    def nested():\n        return hidden_call()\n"
                "    return nested()\n",
                encoding="utf-8",
            )
            graph = CodebaseKnowledgeGraph(str(root))
            graph.build_graph()

            methods = graph.get_symbols("run")
            self.assertEqual(len(methods), 2)
            self.assertEqual(
                {symbol.qualified_name for symbol in methods},
                {"First.run", "Second.run"},
            )
            self.assertIsNotNone(graph.get_symbol("First.run"))

            outer = graph.get_symbol("outer")
            self.assertIsNotNone(outer)
            outer_node = next(
                node_id for node_id, info in graph._symbols.items()
                if info.qualified_name == "outer"
            )
            outer_calls = {
                v for u, v, data in graph.graph.edges(data=True)
                if u == outer_node and data.get("relation") == "calls"
            }
            self.assertIn("nested", outer_calls)
            self.assertNotIn("hidden_call", outer_calls)

    @unittest.skipUnless(HAS_TREE_SITTER, "tree-sitter grammar packages are optional")
    def test_javascript_and_typescript_exported_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "module.js").write_text(
                "export function greet(name) { return name; }\n"
                "export const increment = (value) => value + 1;\n"
                "export class Counter { step(value) { return value + 1; } }\n",
                encoding="utf-8",
            )
            (root / "module.ts").write_text(
                "export function clamp(value: number): number { return value; }\n",
                encoding="utf-8",
            )

            graph = CodebaseKnowledgeGraph(str(root))
            graph.build_graph()
            self.assertIsNotNone(graph.get_symbol("greet"))
            self.assertIsNotNone(graph.get_symbol("increment"))
            self.assertIsNotNone(graph.get_symbol("Counter"))
            self.assertIsNotNone(graph.get_symbol("step"))
            self.assertIsNotNone(graph.get_symbol("clamp"))

    def test_index_truncates_instead_of_growing_past_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = "\n".join(
                f"def function_{n}(value):\n    return value + {n}"
                for n in range(80)
            )
            (root / "many.py").write_text(source, encoding="utf-8")
            graph = CodebaseKnowledgeGraph(str(root))
            graph.build_graph()

            index = graph.get_repository_index(max_tokens=80)
            self.assertIn("index truncated", index)
            self.assertLess(len(index), 600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
