"""
Codebase symbol index for Python and (when tree-sitter grammars are installed)
JavaScript/TypeScript source. It records files, definitions, approximate call
edges, and source line ranges for navigation. It is an index, not semantic
search or a complete dependency analyzer.
"""

from __future__ import annotations

import ast
import os
import posixpath
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
    qualified_name: str = ""
    args: list[str] = field(default_factory=list)
    docstring: Optional[str] = None


class CodebaseKnowledgeGraph:
    """
    Builds a directed graph of files and function/class symbols. Python uses
    the standard AST module; JS/TS uses tree-sitter when available and otherwise
    falls back to basic regular expressions.
    """

    def __init__(self, root_dir: str, skip_dirs: Optional[list[str]] = None):
        self.root_dir = os.path.abspath(root_dir)
        self.skip_dirs = skip_dirs or [
            "venv", ".venv", ".git", "__pycache__", "node_modules", ".env",
            "dist", "build", "coverage", ".pytest_cache",
        ]
        if nx is None:
            raise ImportError(
                "networkx is required for CodebaseKnowledgeGraph. "
                "Install it with: pip install networkx"
            )
        self.graph = nx.DiGraph()
        self._symbols: dict[str, SymbolInfo] = {}
        self._raw_imports: list[tuple[str, str, int, tuple[str, ...], str]] = []
        self._ts_parser = None
        if HAS_TREE_SITTER:
            self._init_tree_sitter()

    def _init_tree_sitter(self):
        """Initialize tree-sitter grammars for supported source languages."""
        try:
            py_language = Language(tspython.language())
            js_language = Language(tsjavascript.language())
            ts_language = Language(tstypescript.language_typescript())
            tsx_language = Language(tstypescript.language_tsx())
            self._ts_parser = Parser()
            self._ts_languages = {
                ".py": py_language,
                ".js": js_language,
                ".jsx": js_language,
                ".ts": ts_language,
                ".tsx": tsx_language,
            }
        except Exception:
            # Python parsing remains available; JS/TS falls back to regex.
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
        self.graph.clear()
        self._symbols.clear()
        self._raw_imports.clear()
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
        self._resolve_import_edges()
        self.resolve_cross_file_edges()
        return files_parsed

    def get_repository_index(self, max_tokens: int = 1000) -> str:
        """Return a compact, approximate token-budgeted file/signature index."""
        lines = ["=== REPOSITORY INDEX (FILES AND SYMBOLS) ==="]
        max_chars = max(0, max_tokens) * 4  # rough character-to-token estimate
        current_len = len(lines[0])
        truncated = False

        for node, data in self.graph.nodes(data=True):
            if data.get("type") != "file":
                continue
            file_entry = f"\n[FILE] {node}"
            if current_len + len(file_entry) > max_chars:
                truncated = True
                break
            lines.append(file_entry)
            current_len += len(file_entry)

            for _, target, edge_data in self.graph.out_edges(node, data=True):
                if edge_data.get("relation") != "defines":
                    continue
                t_data = self.graph.nodes[target]
                sig = t_data.get("signature", target.split("::")[-1])
                lineno = t_data.get("lineno", "?")
                end_lineno = t_data.get("end_lineno", lineno + 15)
                edge_line = f"  └─ {sig} (Lines {lineno}-{end_lineno})"
                if current_len + len(edge_line) > max_chars:
                    truncated = True
                    break
                lines.append(edge_line)
                current_len += len(edge_line)
            if truncated:
                break

        if truncated:
            lines.append("... (index truncated; increase max_tokens for more symbols)")
        return "\n".join(lines)

    def get_holographic_index(self, max_tokens: int = 1000) -> str:
        """Legacy alias for :meth:`get_repository_index`."""
        return self.get_repository_index(max_tokens=max_tokens)

    def find_bridge_path(self, source_symbol: str, target_symbol: str) -> str:
        """
        Return the shortest graph path between the first substring matches.
        Cross-file call edges are approximate, so a path is only a navigation hint.
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

    def get_symbols(self, symbol_name: str) -> list[SymbolInfo]:
        """Return all matches by simple or qualified name."""
        return [
            info for info in self._symbols.values()
            if info.name == symbol_name or info.qualified_name == symbol_name
        ]

    def get_symbol(self, symbol_name: str) -> Optional[SymbolInfo]:
        """Return the first matching symbol; use ``get_symbols`` if ambiguous."""
        matches = self.get_symbols(symbol_name)
        return matches[0] if matches else None

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
        """Parse Python definitions while retaining their lexical scope."""
        try:
            tree = ast.parse(source, filename=rel_path)
        except Exception:
            return

        definition_types = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        for import_node in ast.walk(tree):
            if isinstance(import_node, ast.Import):
                for alias in import_node.names:
                    self._raw_imports.append(
                        (rel_path, alias.name, 0, (), "python")
                    )
            elif isinstance(import_node, ast.ImportFrom):
                imported_names = tuple(alias.name for alias in import_node.names)
                self._raw_imports.append(
                    (
                        rel_path,
                        import_node.module or "",
                        import_node.level,
                        imported_names,
                        "python",
                    )
                )

        def visit(node: ast.AST, scope: tuple[str, ...] = ()) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, definition_types):
                    qualified_name = ".".join((*scope, child.name))
                    self._register_symbol(child, rel_path, qualified_name)
                    visit(child, (*scope, child.name))
                else:
                    visit(child, scope)

        visit(tree)

    def _parse_js_ts(self, source: str, rel_path: str, ext: str) -> None:
        """Parse JS/TS file using tree-sitter (fallback to regex if unavailable)."""
        import_pattern = re.compile(
            r'''(?:\bimport\s+(?:[^'";]*?\s+from\s+)?|\bexport\s+(?:[^'";]*?\s+from\s+))['"]([^'"]+)['"]'''
        )
        for match in import_pattern.finditer(source):
            self._raw_imports.append((rel_path, match.group(1), 0, (), "js"))

        if self._ts_parser is None or ext not in self._ts_languages:
            self._parse_js_ts_regex(source, rel_path)
            return

        try:
            language = self._ts_languages[ext]
            if hasattr(self._ts_parser, "set_language"):
                self._ts_parser.set_language(language)
            else:
                # tree-sitter 0.25+ exposes a writable Parser.language property.
                self._ts_parser.language = language
            tree = self._ts_parser.parse(source.encode("utf-8"))
            seen: set[tuple[str, int, int]] = set()

            def register(node, name_node, symbol_type: str, signature_node=None):
                if name_node is None:
                    return
                name = source[name_node.start_byte:name_node.end_byte].strip()
                if not name:
                    return
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                key = (name, start_line, end_line)
                if key in seen:
                    return
                seen.add(key)
                sig_node = signature_node or node
                signature = source[sig_node.start_byte:sig_node.end_byte].splitlines()[0][:160]
                self._register_js_symbol(
                    name, rel_path, start_line, end_line, signature, symbol_type
                )

            def walk(node):
                if node.type in ("function_declaration", "class_declaration", "method_definition"):
                    register(node, node.child_by_field_name("name"), node.type)
                elif node.type == "variable_declarator":
                    value = node.child_by_field_name("value")
                    if value is not None and value.type in ("arrow_function", "function_expression"):
                        register(value, node.child_by_field_name("name"), value.type, node)
                for child in node.children:
                    walk(child)

            walk(tree.root_node)
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
            if child.type in ("identifier", "property_identifier", "type_identifier"):
                return source[child.start_byte:child.end_byte]
        return "unknown"

    def _register_symbol(
        self, node: ast.AST, rel_path: str, qualified_name: str
    ) -> None:
        name = getattr(node, "name", "<unknown>")
        lineno = getattr(node, "lineno", 0)
        node_id = f"{rel_path}::{qualified_name}@{lineno}"
        args = self._extract_args(node)
        signature = f"{qualified_name}({', '.join(args)})"
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
            qualified_name=qualified_name,
            args=args,
            docstring=docstring,
        )
        self._symbols[node_id] = info

        self.graph.add_node(
            node_id,
            type=type(node).__name__,
            name=name,
            qualified_name=qualified_name,
            file=rel_path,
            lineno=info.lineno,
            end_lineno=info.end_lineno,
            signature=signature,
            docstring=docstring,
        )
        self.graph.add_edge(rel_path, node_id, relation="defines")

        # Track calls in this scope without attributing nested definitions to
        # their enclosing function or class.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            definition_types = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

            def visit_calls(parent: ast.AST) -> None:
                for subnode in ast.iter_child_nodes(parent):
                    if isinstance(subnode, definition_types):
                        continue
                    if isinstance(subnode, ast.Call):
                        func_name = self._call_name(subnode.func)
                        if func_name:
                            self.graph.add_edge(node_id, func_name, relation="calls")
                    visit_calls(subnode)

            for statement in node.body:
                if not isinstance(statement, definition_types):
                    visit_calls(statement)

    def _register_js_symbol(
        self, name: str, rel_path: str, lineno: int, end_lineno: int,
        signature: str, symbol_type: str
    ) -> None:
        node_id = f"{rel_path}::{name}@{lineno}"
        info = SymbolInfo(
            name=name,
            file=rel_path,
            lineno=lineno,
            end_lineno=end_lineno,
            signature=signature,
            symbol_type=symbol_type,
            qualified_name=name,
        )
        self._symbols[node_id] = info

        self.graph.add_node(
            node_id,
            type=symbol_type,
            name=name,
            qualified_name=name,
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

    @staticmethod
    def _python_import_candidates(
        source_file: str,
        module: str,
        level: int,
        imported_names: tuple[str, ...],
    ) -> list[str]:
        """Return plausible local module paths for one Python import statement."""
        source_file = source_file.replace("\\", "/")
        parent = posixpath.dirname(source_file).split("/") if "/" in source_file else []
        if level > 0:
            keep = max(0, len(parent) - (level - 1))
            base_parts = parent[:keep]
        else:
            base_parts = []
        module_parts = [part for part in module.split(".") if part]
        root_parts = base_parts + module_parts
        path_bases: list[list[str]] = []
        if root_parts:
            path_bases.append(root_parts)
        for name in imported_names:
            if name != "*":
                path_bases.append(root_parts + [name.split(".")[0]])
        candidates: list[str] = []
        for parts in path_bases:
            if not parts:
                continue
            stem = "/".join(parts)
            candidates.extend((f"{stem}.py", f"{stem}/__init__.py"))
        return candidates

    @staticmethod
    def _javascript_import_candidates(source_file: str, specifier: str) -> list[str]:
        if not specifier.startswith("."):
            return []  # Bare package imports are not local file dependencies.
        source_file = source_file.replace("\\", "/")
        base = posixpath.normpath(posixpath.join(posixpath.dirname(source_file), specifier))
        extensions = (".js", ".jsx", ".ts", ".tsx")
        candidates = [base] if base.endswith(extensions) else [base + ext for ext in extensions]
        candidates.extend(f"{base}/index{ext}" for ext in extensions)
        return candidates

    def _resolve_import_edges(self) -> int:
        """Resolve imports only to indexed local source files; external imports are ignored."""
        file_nodes = {
            node for node, data in self.graph.nodes(data=True)
            if data.get("type") == "file"
        }
        normalized = {node.replace(os.sep, "/"): node for node in file_nodes}
        resolved = 0
        for source_file, specifier, level, imported_names, language in self._raw_imports:
            source_node = normalized.get(source_file.replace(os.sep, "/"), source_file)
            if language == "python":
                candidates = self._python_import_candidates(
                    source_file, specifier, level, imported_names
                )
            else:
                candidates = self._javascript_import_candidates(source_file, specifier)
            target = next((normalized[item] for item in candidates if item in normalized), None)
            if target is None or target == source_node:
                continue
            if not self.graph.has_edge(source_node, target) or self.graph[source_node][target].get("relation") != "imports_resolved":
                self.graph.add_edge(
                    source_node,
                    target,
                    relation="imports_resolved",
                    specifier=specifier,
                )
                resolved += 1
        return resolved

    def resolve_cross_file_edges(self) -> int:
        """
        Resolve raw call-name edges only when a name has one indexed definition.
        Ambiguous names stay unresolved rather than being guessed.
        """
        defined_funcs: dict[str, list[str]] = {}
        for node_id, data in self.graph.nodes(data=True):
            name = data.get("name")
            if name:
                defined_funcs.setdefault(name, []).append(node_id)

        edges_to_add = []
        for u, v, data in list(self.graph.edges(data=True)):
            targets = defined_funcs.get(v, [])
            if data.get("relation") == "calls" and len(targets) == 1:
                edges_to_add.append(
                    (u, targets[0], {"relation": "calls_resolved"})
                )

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
    print(kg.get_repository_index())
