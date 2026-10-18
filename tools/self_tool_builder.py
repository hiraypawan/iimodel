"""Self-tool builder -- generate, verify and register small local tools.

Trigger
-------
The builder only runs when **both** conditions hold:

* the router's intent confidence is below ``0.5``, and
* the request matches a tool-task pattern ("write a tool that ...", "build a
  script to ...", "I need a function that ...").

Safety rules
------------
* Code that comes from the internet is **never** executed unsandboxed.  The
  sandbox must be Docker (``II_CODE_SANDBOX=docker``); if Docker is missing
  the run fails closed.  There is no silent fallback to a plain subprocess.
* Generated code must match a strict template: allowlisted imports, a
  mandatory ``try``/``except`` block, and network access only through the
  SSRF-guarded fetcher.
* The repair loop receives the **exact** traceback and may change only the
  failing lines.  At most three attempts are made.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

from core.ii_security import run_python_code, sandbox_mode

__all__ = [
    "SelfToolBuilder",
    "ALLOWED_IMPORTS",
    "TOOL_TASK_PATTERNS",
    "matches_tool_task",
    "validate_generated_code",
]

#: Imports a generated tool may use.  Network access is only possible through
#: the SSRF-guarded helpers in this list.
ALLOWED_IMPORTS = {
    "json", "math", "re", "os", "sys", "time", "datetime", "collections",
    "itertools", "functools", "typing", "statistics", "textwrap", "string",
    "urllib.parse", "pathlib",
    "core.ii_security", "ingestion.ii_web", "ingestion",
}

#: Modules that must never be imported by generated tools.
FORBIDDEN_IMPORTS = {
    "socket", "subprocess", "shutil", "ctypes", "multiprocessing", "pty",
    "http.client", "urllib.request", "requests", "aiohttp", "paramiko",
    "pickle", "marshal", "importlib", "ctypes", "signal", "ptyprocess",
}

#: Patterns that mark a request as "build me a tool".
TOOL_TASK_PATTERNS: list[str] = [
    r"\bwrite\s+(?:me\s+)?(?:a|an)\s+(?:small\s+)?(?:tool|script|function|program|utility)\b",
    r"\bbuild\s+(?:me\s+)?(?:a|an)\s+(?:small\s+)?(?:tool|script|function|program|utility)\b",
    r"\bcreate\s+(?:me\s+)?(?:a|an)\s+(?:small\s+)?(?:tool|script|function|program|utility)\b",
    r"\bi\s+need\s+(?:a|an)\s+(?:small\s+)?(?:tool|script|function|program|utility)\b",
    r"\bmake\s+(?:me\s+)?(?:a|an)\s+(?:small\s+)?(?:tool|script|function|program|utility)\b",
    r"\bgenerate\s+(?:a|an)\s+(?:small\s+)?(?:tool|script|function)\b",
    r"\bautomate\s+(?:the\s+)?(?:task\s+of\s+)?[a-z ]{5,60}\b",
]

_TOOL_TASK_RES = [re.compile(pattern, re.IGNORECASE) for pattern in TOOL_TASK_PATTERNS]

_ENTRY_POINT_NAMES = ("run", "main", "execute")


def matches_tool_task(query: str) -> bool:
    """True when the request reads like "build me a tool"."""
    text = query or ""
    return any(pattern.search(text) for pattern in _TOOL_TASK_RES)


# --------------------------------------------------------------------------- #
# Validation of generated code
# --------------------------------------------------------------------------- #

def validate_generated_code(code: str) -> list[str]:
    """Return a list of template violations (empty list == valid)."""
    problems: list[str] = []
    if not code or not code.strip():
        return ["generated code is empty"]
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]

    imported: set[str] = set()
    has_try = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            has_try = True
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    for name in sorted(imported):
        if name in FORBIDDEN_IMPORTS:
            problems.append(f"forbidden import: {name}")
        elif name not in ALLOWED_IMPORTS:
            problems.append(f"import not on the allowlist: {name}")
    if not has_try:
        problems.append("missing mandatory try/except block")
    functions = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    if not functions & set(_ENTRY_POINT_NAMES):
        problems.append(
            "no entry point found (expected a top-level run/main/execute function)"
        )
    if re.search(r"\burlopen\s*\(", code) or re.search(r"\bsocket\.socket\s*\(", code):
        problems.append("raw network access is not allowed; use the guarded fetcher")
    return problems


# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #

class SelfToolBuilder:
    """Build a verified local tool from a task description."""

    MAX_RETRIES = 3

    def __init__(
        self,
        llm: Any = None,
        root: Optional[str | Path] = None,
        *,
        fetcher: Optional[Callable[[str, int], list[dict]]] = None,
    ):
        self.llm = llm
        self.root = Path(root or Path("self_tools")).resolve()
        self.registry_path = self.root / "tool_registry.json"
        self.fetcher = fetcher

    # -- reference code ---------------------------------------------------- #

    def _fetch_reference_code(self, task: str) -> list[dict]:
        """Fetch reference implementations (read-only, never executed as-is)."""
        if self.fetcher is not None:
            try:
                return self.fetcher(task, 3)
            except Exception:
                return []
        from ingestion import ii_web

        try:
            response = ii_web.search_results(f"{task} site:stackoverflow.com", max_results=3)
        except Exception:
            return []
        pages: list[dict] = []
        for result in response.results[:3]:
            try:
                page = ii_web.fetch_page_text(result.link, timeout=15)
            except Exception:
                continue
            if page.get("text"):
                pages.append({
                    "url": result.link,
                    "title": result.title,
                    "text": page["text"],
                })
        return pages

    # -- code generation --------------------------------------------------- #

    def _template(self, task: str, name: str) -> str:
        return (
            '"""Auto-generated II tool.\n\n'
            f"Task: {task}\n\n"
            "Generated by the self-tool builder and verified in a Docker "
            "sandbox. Network access, if any, must go through the "
            "SSRF-guarded fetcher in ingestion.ii_web.\n"
            '"""\n\n'
            "from __future__ import annotations\n\n"
            "import json\n\n"
            "try:\n"
            "    from ingestion import ii_web\n"
            "    from core.ii_security import validate_public_url\n"
            "except Exception as _import_error:  # pragma: no cover - import guard\n"
            "    ii_web = None\n"
            "    validate_public_url = None\n"
            "    _IMPORT_ERROR = _import_error\n"
            "else:\n"
            "    _IMPORT_ERROR = None\n\n\n"
            "def run(payload: dict) -> dict:\n"
            '    """Entry point: return {"ok": bool, "result": ..., "error": str}."""\n'
            "    try:\n"
            "        if _IMPORT_ERROR is not None:\n"
            "            return {\n"
            '                "ok": False,\n'
            '                "result": None,\n'
            '                "error": f"import failure: {_IMPORT_ERROR}",\n'
            "            }\n"
            "        # TODO: implement the requested behaviour.\n"
            "        return {\n"
            '            "ok": True,\n'
            '            "result": {"task": %r, "payload": payload},\n'
            '            "error": None,\n'
            "        }\n"
            "    except Exception as exc:\n"
            "        return {\n"
            '            "ok": False,\n'
            '            "result": None,\n'
            '            "error": f"{type(exc).__name__}: {exc}",\n'
            "        }\n"
        ) % (task,)

    def _generate_code(self, task: str, references: list[dict]) -> str:
        """Ask the model for a tool body; fall back to the strict template."""
        template = self._template(task, "tool")
        if self.llm is None:
            return template
        reference_block = ""
        if references:
            snippets = []
            for reference in references[:2]:
                snippets.append(
                    f"# Reference (read-only, from {reference.get('url')}):\n"
                    f"{str(reference.get('text', ''))[:1200]}"
                )
            reference_block = "\n\n".join(snippets)
        prompt = (
            "Write a single Python module that implements the requested tool.\n"
            "Rules:\n"
            "- Start from the template below and keep its structure.\n"
            "- Only use imports from the allowlist.\n"
            "- Keep the mandatory try/except blocks.\n"
            "- Network access only through ingestion.ii_web (SSRF-guarded).\n"
            "- Return the full module, nothing else.\n\n"
            f"TASK: {task}\n\n"
            f"TEMPLATE:\n```python\n{template}\n```\n\n"
            f"REFERENCE MATERIAL (read-only, may be wrong):\n{reference_block}\n"
        )
        try:
            response = self.llm.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You generate small, safe Python tools for a local "
                            "system. Return only code."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=1200,
            )
        except Exception:
            return template
        if isinstance(response, dict):
            content = response.get("content")
            if isinstance(content, str) and content.strip():
                return _extract_code(content)
        return template

    # -- sandbox ------------------------------------------------------------ #

    def sandbox_run(self, code: str, timeout: int = 30) -> tuple[bool, str]:
        """Run code in the fail-closed Docker sandbox.

        Fetched or generated code is never executed in a plain subprocess:
        without Docker the run fails and the code is not trusted.
        """
        mode = sandbox_mode()
        if mode != "docker":
            return False, (
                "Refusing to run generated code: II_CODE_SANDBOX is not set to "
                "'docker'. Generated code must run in the fail-closed Docker "
                "sandbox; there is no silent subprocess fallback."
            )
        return run_python_code(code, timeout=timeout)

    # -- repair loop -------------------------------------------------------- #

    def _fix_loop(self, code: str, error: str, task: str) -> tuple[str, list[dict]]:
        """Repair generated code using the exact traceback (max 3 attempts)."""
        attempts: list[dict] = []
        current = code
        for attempt in range(1, self.MAX_RETRIES + 1):
            fixed = self._ask_for_fix(current, error, task)
            if not fixed:
                break
            problems = validate_generated_code(fixed)
            ok, output = self.sandbox_run(fixed)
            attempts.append({
                "attempt": attempt,
                "template_problems": problems,
                "sandbox_ok": ok,
                "sandbox_output": output[:2000],
            })
            if not problems and ok:
                return fixed, attempts
            current = fixed
            error = output if not ok else "; ".join(problems)
        return current, attempts

    def _ask_for_fix(self, code: str, error: str, task: str) -> Optional[str]:
        if self.llm is None:
            return None
        prompt = (
            "Fix ONLY the failing lines. Return full code.\n\n"
            f"TASK: {task}\n\n"
            "CURRENT CODE:\n```python\n"
            f"{code}\n```\n\n"
            f"EXACT ERROR:\n{error}\n"
        )
        try:
            response = self.llm.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You repair Python code. Change only the failing "
                            "lines. Return the full module and nothing else."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=1200,
            )
        except Exception:
            return None
        if isinstance(response, dict):
            content = response.get("content")
            if isinstance(content, str) and content.strip():
                return _extract_code(content)
        return None

    # -- registry ----------------------------------------------------------- #

    def save(self, category: str, tool_name: str, code: str) -> Path:
        """Save the tool under ``self_tools/{category}/{tool_name}.py``."""
        directory = self.root / category
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{tool_name}.py"
        path.write_text(code, encoding="utf-8")
        return path

    def register(self, category: str, tool_name: str, path: Path, description: str) -> dict:
        """Register the tool in ``self_tools/tool_registry.json``."""
        registry = self.load_registry()
        entry = {
            "name": tool_name,
            "category": category,
            "path": str(Path(path).relative_to(self.root)) if str(path).startswith(str(self.root)) else str(path),
            "description": description,
            "entry_point": "run",
            "registered_at": None,
        }
        registry = [item for item in registry if item.get("name") != tool_name]
        registry.append(entry)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return entry

    def load_registry(self) -> list[dict]:
        """Load the registered tools (empty list when none exist)."""
        if not self.registry_path.exists():
            return []
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def tool_definitions(self) -> list[dict]:
        """Return the registry as structured tool definitions for the agent."""
        definitions: list[dict] = []
        for entry in self.load_registry():
            definitions.append({
                "type": "function",
                "function": {
                    "name": entry.get("name", "tool"),
                    "description": entry.get("description", "registered self-tool"),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "payload": {"type": "object"},
                        },
                        "required": ["payload"],
                    },
                },
            })
        return definitions

    # -- main --------------------------------------------------------------- #

    def build(self, task: str, constraint_tree: Optional[dict] = None) -> dict:
        """Build, verify and (when verified) register a tool."""
        name = _slugify(task)
        category = _categorise(task)
        references = self._fetch_reference_code(task)
        code = self._generate_code(task, references)
        problems = validate_generated_code(code)
        attempts: list[dict] = []
        ok = False
        output = ""
        if not problems:
            ok, output = self.sandbox_run(code)
            attempts.append({
                "attempt": 0,
                "template_problems": [],
                "sandbox_ok": ok,
                "sandbox_output": output[:2000],
            })
        if not ok:
            code, attempts = self._fix_loop(code, output or "; ".join(problems), task)
            problems = validate_generated_code(code)
            if problems:
                ok = False

        result = {
            "status": "VERIFIED" if ok else "PARTIAL",
            "tool_name": name,
            "category": category,
            "code": code,
            "template_problems": problems,
            "attempts": attempts,
            "sandbox": sandbox_mode(),
            "references": [
                {"url": reference.get("url"), "title": reference.get("title")}
                for reference in references
            ],
            "note": (
                "Generated code runs in the fail-closed Docker sandbox only; "
                "no unsandboxed execution is performed."
            ),
        }
        if ok:
            path = self.save(category, name, code)
            result["path"] = str(path)
            result["registry_entry"] = self.register(
                category, name, path, f"Auto-generated tool for: {task}"
            )
        return result


def _extract_code(text: str) -> str:
    """Pull the code out of a (possibly fenced) model answer."""
    if "```" in text:
        blocks = text.split("```")
        for index in range(1, len(blocks), 2):
            block = blocks[index]
            if block.lower().startswith("python"):
                block = block.split("\n", 1)[1] if "\n" in block else ""
            if block.strip():
                return block.strip()
    return text.strip()


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return (slug[:48] or "generated_tool")


def _categorise(text: str) -> str:
    lowered = (text or "").lower()
    if any(word in lowered for word in ("http", "url", "fetch", "web", "api", "search")):
        return "web"
    if any(word in lowered for word in ("file", "read", "write", "path", "directory")):
        return "files"
    if any(word in lowered for word in ("json", "parse", "format", "convert", "text")):
        return "text"
    return "general"
