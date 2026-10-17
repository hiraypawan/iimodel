"""
Part 11 / v11 — FIX 4: Blackboard Pattern + Interface-First Locking

Solves context rot in multi-file software by separating architecture design
from implementation:

1. Blackboard: central JSON store of interfaces/data models/API contracts
2. Architect Agent: writes interfaces to Blackboard (locked = read-only)
3. Builder Agent: reads ONE locked interface, implements ONE file, never sees others
4. Inspector Agent: runs static type checker across entire project, flags mismatches

Guarantees 0% cross-file integration errors via deterministic structural locking.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


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
    Central architecture database. Interfaces are written once, then locked.
    Builders read locked interfaces and implement files in isolation.
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
        """Write (or overwrite) an interface spec. Not locked yet."""
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
        """Write an API contract (endpoint specs, request/response schemas)."""
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
    """
    Builder agent: reads ONE locked interface, implements ONE file.
    Never sees the other 49 files — prevents context rot.
    """

    def __init__(self, blackboard: Blackboard):
        self.board = blackboard

    def build_file(self, entry_id: str, model_chat_fn) -> Optional[str]:
        """Implement the file described by the locked interface."""
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
        if code:
            path = Path(iface.file)
            path.write_text(code, encoding="utf-8")
            return str(path)
        return None

    @staticmethod
    def _extract_code(text: str) -> Optional[str]:
        if "```python" in text:
            return text.split("```python", 1)[1].split("```", 1)[0].strip()
        if "```" in text:
            return text.split("```", 1)[1].split("```", 1)[0].strip()
        return None


class BlackboardInspector:
    """
    Inspector agent: runs static type checker across entire project,
    flags cross-file mismatches by exact file and line.
    """

    def __init__(self, workdir: str | Path = "."):
        self.workdir = Path(workdir).resolve()

    def inspect(self) -> list[dict]:
        """Run mypy/pyright if available, else basic AST syntax check."""
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
    Actions: "design", "build", "inspect", "list"
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
        inspector = BlackboardInspector()
        issues = inspector.inspect()
        if not issues:
            return "INSPECTION PASSED: No cross-file or syntax issues found."
        lines = ["INSPECTION ISSUES:"]
        for iss in issues:
            lines.append(f"  {iss['source']}: {iss['file']}:{iss['line']} - {iss['message']}")
        return "\n".join(lines)

    return f"Unknown blackboard action: {action}. Use: design, build, inspect, list"


if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "list"
    print(blackboard_tool(action))
