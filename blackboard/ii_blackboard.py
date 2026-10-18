"""
Experimental blackboard helpers for storing interface specifications and running
basic project inspection. The current API supports listing and inspection; the
architect/builder classes are not yet wired into the web or CLI workflows.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass
class InterfaceSpec:
    name: str
    file: str
    kind: str  # "class", "function", "api_endpoint", "data_model"
    signature: str
    docstring: str
    locked: bool = False


@dataclass
class BlackboardEntry:
    id: str
    type: str  # "interface", "contract", "data_model"
    content: dict
    locked: bool = False
    timestamp: str = ""


class Blackboard:
    """
    Small JSON-backed store for interface records. Lock checks are enforced by
    the public write methods, but this store does not validate generated code.
    """

    def __init__(self, workdir: str | Path = "."):
        self.workdir = Path(workdir).resolve()
        self.board_path = self.workdir / ".blackboard.json"
        self.entries: dict[str, BlackboardEntry] = {}
        self._load()

    def _load(self):
        if self.board_path.exists():
            try:
                data = json.loads(self.board_path.read_text(encoding="utf-8"))
                for k, v in data.items():
                    self.entries[k] = BlackboardEntry(**v)
            except (json.JSONDecodeError, TypeError):
                self.entries = {}

    def save(self):
        data = {k: asdict(v) for k, v in self.entries.items()}
        self.board_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def write_interface(
        self,
        entry_id: str,
        iface: InterfaceSpec,
    ) -> str:
        """Write an interface spec unless an existing record is locked."""
        existing = self.entries.get(entry_id)
        if existing is not None and existing.locked:
            raise ValueError(f"Interface {entry_id!r} is locked and cannot be overwritten.")
        entry = BlackboardEntry(
            id=entry_id,
            type="interface",
            content=asdict(iface),
            locked=False,
            timestamp=self._now(),
        )
        self.entries[entry_id] = entry
        self.save()
        return entry_id

    def lock_interface(self, entry_id: str) -> bool:
        """Lock an interface so it becomes read-only (no more changes)."""
        if entry_id in self.entries:
            self.entries[entry_id].locked = True
            self.entries[entry_id].timestamp = self._now()
            self.save()
            return True
        return False

    def get_locked_interface(self, entry_id: str) -> Optional[InterfaceSpec]:
        """Read a locked interface. Returns None if not locked or missing."""
        entry = self.entries.get(entry_id)
        if entry is None or not entry.locked or entry.type != "interface":
            return None
        return InterfaceSpec(**entry.content)

    def list_locked_interfaces(self) -> list[InterfaceSpec]:
        """Return all locked interfaces."""
        out = []
        for e in self.entries.values():
            if e.locked and e.type == "interface":
                out.append(InterfaceSpec(**e.content))
        return out

    def write_contract(
        self,
        entry_id: str,
        content: dict,
    ) -> str:
        """Write an API contract unless an existing record is locked."""
        existing = self.entries.get(entry_id)
        if existing is not None and existing.locked:
            raise ValueError(f"Contract {entry_id!r} is locked and cannot be overwritten.")
        entry = BlackboardEntry(
            id=entry_id,
            type="contract",
            content=content,
            locked=False,
            timestamp=self._now(),
        )
        self.entries[entry_id] = entry
        self.save()
        return entry_id

    def lock_contract(self, entry_id: str) -> bool:
        if entry_id in self.entries:
            self.entries[entry_id].locked = True
            self.entries[entry_id].timestamp = self._now()
            self.save()
            return True
        return False

    def get_all_locked(self) -> list[BlackboardEntry]:
        return [e for e in self.entries.values() if e.locked]

    @staticmethod
    def _now() -> str:
        import time
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class BlackboardArchitect:
    """
    Architect agent: designs the system and writes interfaces to the Blackboard.
    """

    def __init__(self, blackboard: Blackboard):
        self.board = blackboard

    def design_system(
        self,
        requirements: str,
        model_chat_fn,
    ) -> list[str]:
        """
        Ask the model to design interfaces for the system, write them to the
        Blackboard, and lock them. Returns list of locked interface IDs.
        """
        prompt = (
            "You are the Architect Agent. Design the system interfaces for:\n"
            f"{requirements}\n\n"
            "Return a JSON array of interface specs, each with: name, file, kind, "
            "signature, docstring. Do NOT write implementation code — only interfaces.\n"
        )
        response = model_chat_fn(prompt)

        # Parse interface specs from model response (best-effort JSON extraction)
        interface_ids = []
        try:
            import re, json
            match = re.search(r"\[.*\]", response, re.DOTALL)
            if match:
                specs = json.loads(match.group(0))
                for i, s in enumerate(specs):
                    iface = InterfaceSpec(
                        name=s.get("name", f"iface_{i}"),
                        file=s.get("file", f"iface_{i}.py"),
                        kind=s.get("kind", "class"),
                        signature=s.get("signature", ""),
                        docstring=s.get("docstring", ""),
                    )
                    eid = self.board.write_interface(f"iface_{i}", iface)
                    self.board.lock_interface(eid)
                    interface_ids.append(eid)
        except Exception:
            pass

        return interface_ids


class BlackboardBuilder:
    """Draft one file from a locked interface using a caller-supplied model.

    The prompt is scoped to that interface, but the model context itself is not
    isolated from other files or conversation history.
    """

    def __init__(self, blackboard: Blackboard):
        self.board = blackboard

    def build_file(
        self,
        entry_id: str,
        model_chat_fn: Callable[[str], str],
        validation_fn: Callable[[Path], bool | tuple[bool, str]] | None = None,
    ) -> Optional[str]:
        """Write one statically parsed candidate and optionally roll back on failure.

        ``validation_fn`` is an opt-in caller hook. If it executes candidate
        code, it must provide genuine OS/container isolation; this class does
        not supply a sandbox. A failed validation restores the prior file, using
        ``git restore --worktree`` only when the file was clean relative to the
        index before this operation, otherwise restoring an exact byte snapshot.
        """
        iface = self.board.get_locked_interface(entry_id)
        if iface is None:
            return None

        prompt = (
            "You are the Builder Agent. Implement this single file based on the "
            f"LOCKED interface specification below. Do NOT reference any other files.\n\n"
            f"FILE: {iface.file}\n"
            f"KIND: {iface.kind}\n"
            f"SIGNATURE: {iface.signature}\n"
            f"DOCSTRING: {iface.docstring}\n\n"
            "Return ONLY the complete file contents in a ```python block."
        )
        response = model_chat_fn(prompt)
        code = self._extract_code(response)
        if not code:
            return None

        from reasoning.verification_cascade import VerificationCascade
        static_report = VerificationCascade().verify_python(code)
        if not static_report.passed:
            self.last_build_status = "Static syntax verification failed; file was not written."
            return None

        path = (self.board.workdir / iface.file).resolve()
        if path != self.board.workdir and self.board.workdir not in path.parents:
            raise ValueError("Interface file path must stay inside the configured workdir.")
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.is_file()
        original_bytes = path.read_bytes() if existed else None
        original_mode = path.stat().st_mode if existed else None
        relative = path.relative_to(self.board.workdir).as_posix()
        git_clean = self._tracked_and_clean(relative)
        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as file:
                file.write(code)
                temp_path = file.name
            os.replace(temp_path, path)
            temp_path = None

            if validation_fn is not None:
                verdict = validation_fn(path)
                passed = verdict[0] if isinstance(verdict, tuple) else bool(verdict)
                if not passed:
                    self._restore_snapshot(path, existed, original_bytes, original_mode, relative, git_clean)
                    self.last_build_status = "Validation failed; original file restored."
                    return None
            self.last_build_status = "Static checks passed; candidate file written."
            return str(path)
        except Exception as exc:
            self._restore_snapshot(path, existed, original_bytes, original_mode, relative, git_clean)
            self.last_build_status = f"Build validation raised {type(exc).__name__}; original file restored."
            return None
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def _tracked_and_clean(self, relative_path: str) -> bool:
        try:
            tracked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", "--", relative_path],
                cwd=self.board.workdir, capture_output=True, text=True, timeout=5,
            )
            clean = subprocess.run(
                ["git", "diff", "--quiet", "--", relative_path],
                cwd=self.board.workdir, capture_output=True, timeout=5,
            )
            return tracked.returncode == 0 and clean.returncode == 0
        except (FileNotFoundError, subprocess.SubprocessError):
            return False

    def _restore_snapshot(
        self,
        path: Path,
        existed: bool,
        original_bytes: bytes | None,
        original_mode: int | None,
        relative_path: str,
        git_clean: bool,
    ) -> None:
        if not existed:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        if git_clean:
            try:
                restored = subprocess.run(
                    ["git", "restore", "--worktree", "--", relative_path],
                    cwd=self.board.workdir, capture_output=True, text=True, timeout=5,
                )
                if restored.returncode == 0:
                    return
            except (FileNotFoundError, subprocess.SubprocessError):
                pass
        if original_bytes is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(original_bytes)
            if original_mode is not None:
                path.chmod(original_mode)


    @staticmethod
    def _extract_code(text: str) -> Optional[str]:
        if "```python" in text:
            return text.split("```python", 1)[1].split("```", 1)[0].strip()
        if "```" in text:
            return text.split("```", 1)[1].split("```", 1)[0].strip()
        return None


class BlackboardInspector:
    """Return an approximate dependency graph and run a limited static check.

    If mypy is installed it is invoked; otherwise only Python syntax is checked.
    Neither path guarantees that cross-file contracts or runtime behavior are
    correct.
    """

    def __init__(self, workdir: str | Path = "."):
        self.workdir = Path(workdir).resolve()

    def dependency_graph(self) -> dict:
        """Return an approximate, serializable file/symbol graph for review."""
        from graph.topology import GraphTopologyAnalyzer
        from graph.ii_graph import CodebaseKnowledgeGraph

        indexer = CodebaseKnowledgeGraph(str(self.workdir))
        indexer.build_graph()
        analyzer = GraphTopologyAnalyzer(indexer.graph)
        nodes = [
            {"id": str(node), **{
                key: value for key, value in attributes.items()
                if isinstance(value, (str, int, float, bool, type(None)))
            }}
            for node, attributes in indexer.graph.nodes(data=True)
        ]
        edges = [
            {"source": str(source), "target": str(target), **dict(attributes)}
            for source, target, attributes in indexer.graph.edges(data=True)
            if attributes.get("relation") in {"calls_resolved", "imports_resolved"}
        ]
        return {
            "approximate": True,
            "architecture_map": analyzer.generate_architecture_map(),
            "cycles": analyzer.find_cycles(),
            "clusters": analyzer.cluster_subsystems(),
            "nodes": nodes,
            "edges": edges,
        }

    def inspect(self) -> list[dict]:
        """Run mypy if available, else basic Python AST syntax checks."""
        issues = []

        # Try mypy first
        try:
            result = subprocess.run(
                ["mypy", str(self.workdir)],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                for line in result.stdout.strip().splitlines():
                    issues.append({"file": "", "line": "", "message": line, "source": "mypy"})
            return issues
        except FileNotFoundError:
            pass

        # Fallback: AST syntax check on all .py files
        for py_file in self.workdir.rglob("*.py"):
            if any(skip in str(py_file) for skip in (".git", "__pycache__", "venv", ".blackboard.json")):
                continue
            try:
                ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                issues.append({
                    "file": str(py_file),
                    "line": str(exc.lineno),
                    "message": exc.msg,
                    "source": "ast",
                })

        return issues


def blackboard_tool(action: str, payload: str = "", workdir: str | Path = ".") -> str:
    """
    High-level tool entry point for the II agent.
    Actions: "inspect", "list", and an approximate "graph" summary.
    """
    board = Blackboard(workdir)

    if action == "list":
        locked = board.list_locked_interfaces()
        if not locked:
            return "No locked interfaces on the Blackboard."
        lines = ["Locked Interfaces:"]
        for iface in locked:
            lines.append(f"  - {iface.name} ({iface.file}) [{iface.kind}]")
        return "\n".join(lines)

    if action == "inspect":
        inspector = BlackboardInspector(workdir)
        issues = inspector.inspect()
        if not issues:
            return "INSPECTION COMPLETE: No issues reported by the available checker; correctness is not established."
        lines = ["INSPECTION ISSUES:"]
        for iss in issues:
            lines.append(f"  {iss['source']}: {iss['file']}:{iss['line']} - {iss['message']}")
        return "\n".join(lines)

    if action == "graph":
        return json.dumps(BlackboardInspector(workdir).dependency_graph(), indent=2)

    return f"Unknown blackboard action: {action}. Use: inspect, list, graph"


if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "list"
    print(blackboard_tool(action))
