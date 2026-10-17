"""
Module 1: ii_graph.py — NetworkX Codebase Knowledge Graph Builder

Scans a Python/JavaScript/TypeScript codebase and builds a directed call +
definition graph. Provides:
  - Holographic Index (Level 1: Files + Level 2: Signatures) — <1,000 tokens
  - Cross-file bridge path resolution (hidden dependencies)
  - Semantic proximity hooks (embeddings-based edges)
  - Cross-language support: Python, JavaScript, TypeScript, JSX, TSX

Used by the II orchestrator to give the 1.5B model "infinite effective context"
via surgical code slicing instead of raw context stuffing.

v12: Adds tree-sitter based cross-language parsing for JS/TS/TSX/JSX.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from typing import Optional

try:
    import networkx as nx
except ImportError:  # pragma: no cover - networkx is optional at import time
    nx = None  # type: ignore[assignment]

# Optional tree-sitter for cross-language parsing
try:
    from tree_sitter import Language, Parser
    import tree_sitter_python as tspython
    import tree_sitter_javascript as tsjavascript
    import tree_sitter_typescript as tstypescript
    HAS_TREE_SITTER = True
except ImportError:
    HAS_TREE_SITTER = False


@dataclass
class SymbolInfo:
    """Metadata for a function/class symbol in the codebase."""
    name: str
    file: str
    lineno: int
    end_lineno: int
    signature: str
    symbol_type: str  # "FunctionDef", "AsyncFunctionDef", "ClassDef", etc.
    args: list[str] = field(default_factory=list)
    docstring: Optional[str] = None


class CodebaseKnowledgeGraph:
    """
    Builds a directed graph of a codebase:
      Nodes: files and symbols (functions/classes)
      Edges: file->symbol (defines), symbol->symbol (calls)
    Supports Python (via ast) and JS/TS (via tree-sitter).
    """

    def __init__(self, root_dir: str, skip_dirs: Optional[list[str]] = None):
        self.root_dir = os.path.abspath(root_dir)
        self.skip_dirs = skip_dirs or ["venv", ".git", "__pycache__", "node_modules", ".env"]
        if nx is None:
            raise ImportError(
                "networkx is required for CodebaseKnowledgeGraph. "
                "Install it with: pip install networkx"
            )
        self.graph = nx.DiGraph()
        self._symbols: dict[str, SymbolInfo] = {}
        self._ts_parser = None
        if HAS_TREE_SITTER:
            self._init_tree_sitter()

    def _init_tree_sitter(self):
        """Initialize tree-sitter parsers for cross-language support."""
        try:
            PY_LANGUAGE = Language(tspython.language())
            JS_LANGUAGE = Language(tsjavascript.language())
            TS_LANGUAGE = Language(tstypescript.language())
            self._ts_parser = Parser()
            self._ts_languages = {
                ".py": PY_LANGUAGE,
                ".js": JS_LANGUAGE,
                ".jsx": JS_LANGUAGE,
                ".ts": TS_LANGUAGE,
                ".tsx": TS_LANGUAGE,
            }
        except Exception:
            self._ts_parser = None
            self._ts_languages = {}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def build_graph(self) -> int:
        """
        Walk root_dir, parse every supported file, and populate the graph.
        Returns the number of files parsed.
        """
        files_parsed = 0
        for dirpath, dirnames, filenames in os.walk(self.root_dir):
            # Prune skip directories in-place
            dirnames[:] = [
                d for d in dirnames
                if not any(skip in d for skip in self.skip_dirs)
            ]
            for filename in filenames:
                if not filename.endswith(
                    (".py", ".js", ".ts", ".jsx", ".tsx")
                ):
                    continue
                full_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(full_path, self.root_dir)
                self._parse_file(full_path, rel_path)
                files_parsed += 1
        self.resolve_cross_file_edges()
        return files_parsed

    def get_holographic_index(self, max_tokens: int = 1000) -> str:
        """
        Return Level 1 (Files) + Level 2 (Signatures) as a compact text map.
        Designed to fit within the small model's effective context window.
        """
        lines = ["=== HOLOGRAPHIC REPO INDEX (LEVEL 1 & 2) ==="]
        current_len = len(lines[0])

        for node, data in self.graph.nodes(data=True):
            if data.get("type") != "file":
                continue
            file_entry = f"\n[FILE] {node}"
            lines.append(file_entry)
            current_len += len(file_entry)

            for _, target in self.graph.out_edges(node):
                t_data = self.graph.nodes[target]
                sig = t_data.get("signature", target.split("::")[-1])
                lineno = t_data.get("lineno", "?")
                end_lineno = t_data.get("end_lineno", lineno + 15)
                edge_line = f"  └─ {sig} (Lines {lineno}-{end_lineno})"
                lines.append(edge_line)
                current_len += len(edge_line)

                if current_len > max_tokens * 4:  # rough char->token estimate
                    lines.append("  ... (index truncated for context budget)")
                    break

        return "\n".join(lines)

    def find_bridge_path(self, source_symbol: str, target_symbol: str) -> str:
        """
        Find the shortest dependency path between two symbols across files.
        Useful for surfacing hidden cross-file connections (e.g. bug in File #1
        affecting File #48).
        """
        undirected = self.graph.to_undirected()
        matches_src = [n for n in self.graph.nodes if source_symbol in n]
        matches_tgt = [n for n in self.graph.nodes if target_symbol in n]
        if not matches_src or not matches_tgt:
            return "Symbol not found in graph."
        try:
            path = nx.shortest_path(undirected, matches_src[0], matches_tgt[0])
            return " -> ".join(path)
        except nx.NetworkXNoPath:
            return "No direct dependency path found."

    def get_symbol(self, symbol_name: str) -> Optional[SymbolInfo]:
        """Look up a symbol's exact file + line range by name."""
        for node_id, info in self._symbols.items():
            if info.name == symbol_name:
                return info
        return None

    def get_symbols_in_file(self, rel_path: str) -> list[SymbolInfo]:
        """Return all symbols defined in a given file."""
        return [
            info for info in self._symbols.values() if info.file == rel_path
        ]

    # ------------------------------------------------------------------ #
    # Internal parsing
    # ------------------------------------------------------------------ #
    def _parse_file(self, full_path: str, rel_path: str) -> None:
        ext = os.path.splitext(full_path)[1].lower()
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                source = f.read()
        except Exception:
            return  # skip files that fail to parse

        # Level 1 node: the file itself
        self.graph.add_node(rel_path, type="file")

        if ext == ".py":
            self._parse_python(source, rel_path)
        elif ext in (".js", ".jsx", ".ts", ".tsx"):
            self._parse_js_ts(source, rel_path, ext)

    def _parse_python(self, source: str, rel_path: str) -> None:
        """Parse Python file using built-in ast module."""
        try:
            tree = ast.parse(source, filename=rel_path)
        except Exception:
            return

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self._register_symbol(node, rel_path)

    def _parse_js_ts(self, source: str, rel_path: str, ext: str) -> None:
        """Parse JS/TS file using tree-sitter (fallback to regex if unavailable)."""
        if self._ts_parser is None or ext not in self._ts_languages:
            self._parse_js_ts_regex(source, rel_path)
            return

        try:
            self._ts_parser.set_language(self._ts_languages[ext])
            tree = self._ts_parser.parse(bytes(source, "utf8"))

            for node in tree.root_node.children:
                if node.type in (
                    "function_declaration",
                    "arrow_function",
                    "method_definition",
                    "class_declaration",
                ):
                    name = self._extract_js_name(node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    signature = source[
                        node.start_byte:node.end_byte
                    ].split("\n")[0][:100]
                    self._register_js_symbol(
                        name, rel_path, start_line, end_line, signature, node.type
                    )
        except Exception:
            self._parse_js_ts_regex(source, rel_path)

    def _parse_js_ts_regex(self, source: str, rel_path: str) -> None:
        """Fallback regex-based parsing for JS/TS when tree-sitter unavailable."""
        # Match function declarations and class declarations
        patterns = [
            r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(",
            r"(?:export\s+)?(?:async\s+)?(\w+)\s*[:=]\s*\([^)]*\)\s*=>",
            r"(?:export\s+)?class\s+(\w+)",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, source, re.MULTILINE):
                name = match.group(1)
                start_line = source[: match.start()].count("\n") + 1
                end_line = start_line + 5  # estimate
                signature = match.group(0)[:100]
                self._register_js_symbol(
                    name, rel_path, start_line, end_line, signature, "function"
                )

    def _extract_js_name(self, node, source: str) -> str:
        """Extract symbol name from tree-sitter node."""
        for child in node.children:
            if child.type == "identifier":
                return source[child.start_byte:child.end_byte]
        return "unknown"

    def _register_symbol(self, node: ast.AST, rel_path: str) -> None:
        name = getattr(node, "name", "<unknown>")
        node_id = f"{rel_path}::{name}"
        args = self._extract_args(node)
        signature = f"{name}({', '.join(args)})"
        docstring = ast.get_docstring(node) if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ) else None

        info = SymbolInfo(
            name=name,
            file=rel_path,
            lineno=getattr(node, "lineno", 0),
            end_lineno=getattr(node, "end_lineno", getattr(node, "lineno", 0) + 15),
            signature=signature,
            symbol_type=type(node).__name__,
            args=args,
            docstring=docstring,
        )
        self._symbols[node_id] = info

        self.graph.add_node(
            node_id,
            type=type(node).__name__,
            file=rel_path,
            lineno=info.lineno,
            end_lineno=info.end_lineno,
            signature=signature,
            docstring=docstring,
        )
        self.graph.add_edge(rel_path, node_id, relation="defines")

        # Track function calls inside this symbol (Level 3: call-flow edges)
        for subnode in ast.walk(node):
            if isinstance(subnode, ast.Call):
                func_name = self._call_name(subnode.func)
                if func_name:
                    self.graph.add_edge(node_id, func_name, relation="calls")

    def _register_js_symbol(
        self, name: str, rel_path: str, lineno: int, end_lineno: int,
        signature: str, symbol_type: str
    ) -> None:
        node_id = f"{rel_path}::{name}"
        info = SymbolInfo(
            name=name,
            file=rel_path,
            lineno=lineno,
            end_lineno=end_lineno,
            signature=signature,
            symbol_type=symbol_type,
        )
        self._symbols[node_id] = info

        self.graph.add_node(
            node_id,
            type=symbol_type,
            file=rel_path,
            lineno=lineno,
            end_lineno=end_lineno,
            signature=signature,
        )
        self.graph.add_edge(rel_path, node_id, relation="defines")

    @staticmethod
    def _extract_args(node: ast.AST) -> list[str]:
        args_node = getattr(node, "args", None)
        if args_node is None:
            return []
        out: list[str] = []
        for a in getattr(args_node, "args", []):
            out.append(getattr(a, "arg", ""))
        if getattr(args_node, "vararg", None):
            out.append("*" + args_node.vararg.arg)
        if getattr(args_node, "kwarg", None):
            out.append("**" + args_node.kwarg.arg)
        return out

    @staticmethod
    def _call_name(func_node: ast.AST) -> Optional[str]:
        if isinstance(func_node, ast.Name):
            return func_node.id
        if isinstance(func_node, ast.Attribute):
            return func_node.attr
        return None

    def resolve_cross_file_edges(self) -> int:
        """
        Link raw 'calls' edges (symbol name only) to their actual
        File::Symbol target nodes, producing 'calls_resolved' edges.
        Returns the number of resolved edges added.
        """
        defined_funcs: dict[str, str] = {}
        for node_id in self.graph.nodes:
            if "::" in node_id:
                _, sym_name = node_id.rsplit("::", 1)
                defined_funcs.setdefault(sym_name, node_id)

        edges_to_add = []
        for u, v, data in list(self.graph.edges(data=True)):
            if data.get("relation") == "calls" and v in defined_funcs:
                target = defined_funcs[v]
                edges_to_add.append((u, target, {"relation": "calls_resolved"}))

        self.graph.add_edges_from(edges_to_add)
        return len(edges_to_add)


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    kg = CodebaseKnowledgeGraph(root)
    n = kg.build_graph()
    print(f"Parsed {n} files.")
    print(kg.get_holographic_index())
