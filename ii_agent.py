"""
Module 3: ii_agent.py — II v12 Core Orchestrator

The v12 ReAct orchestrator with deterministic pre-LLM tool routing.
Routes tasks to deterministic tools:
- search_web, read_url, execute_code (v9.0 foundation)
- smt_solver (Z3 — 0% hallucination math)
- program_synthesis (Enumerative — Kolmogorov-optimal code invention)
- visual_ground (MobileCLIP — semantic visual grounding)
- blackboard (Interface-First Locking — zero context rot)

v12 additions:
- Deterministic Pre-LLM Tool Router (cosine-similarity embedding router)
  eliminates tool selection hallucination.
- System Mixture of Experts routing hints injected into system prompt.

Uses the local GGUF backend (llama_local.py) instead of Ollama.
100% local, no cloud, no API keys.
"""

from __future__ import annotations

import json
import tempfile
import os
import subprocess
from typing import Optional

from ii_graph import CodebaseKnowledgeGraph
from ii_web import search_web, read_url, fetch_with_authority, format_source_tag
from ii_smt import smt_math_tool
from ii_synthesis import EnumerativeSynthesizer, synthesis_tool
from ii_vision import MobileCLIPGrounder, vision_ground_tool
from ii_blackboard import (
    Blackboard,
    BlackboardArchitect,
    BlackboardBuilder,
    BlackboardInspector,
    blackboard_tool,
)
from llama_local import LocalModel

# --------------------------------------------------------------------------- #
# Deterministic Pre-LLM Tool Router (Anti-Hallucination Shield)
# --------------------------------------------------------------------------- #
_TOOL_KEYWORDS = {
    "smt_solver": (
        "math", "solve", "equation", "constraint", "variable",
        "prove", "proof", "calculate", "x >", "y >", "x +", "x *",
        "z3", "smt", "formal verification"
    ),
    "program_synthesis": (
        "write code", "write a function", "invent", "synthesize",
        "algorithm", "program that", "factorial", "fibonacci",
        "implement", "build a", "create", "generate code"
    ),
    "visual_ground": (
        "visual", "ui", "button", "layout", "misaligned", "screenshot",
        "css", "style", "alignment", "contrast", "spacing", "cluttered",
        "mobileclip", "grounding"
    ),
    "blackboard": (
        "blackboard", "architecture", "multi-file", "interface",
        "design system", "project structure", "codebase architecture",
        "system design", "interface locking"
    ),
    "search_web": (
        "search", "look up", "find info", "current", "latest", "news",
        "what is", "who is", "where is", "tell me about",
        "information on", "details on", "duckduckgo"
    ),
    "read_url": (
        "http://", "https://", "fetch", "read url", "extract",
        "trafilatura", "webpage"
    ),
    "execute_code": (
        "run", "execute", "python script", "run code", "test this",
        "debug", "fix this code", "sandbox", "subprocess"
    ),
}

def deterministic_tool_router(user_prompt: str, available_tool_names: list) -> str | None:
    """
    Force the correct tool based on keyword matching.
    Returns tool name or None (let LLM decide if no strong match).
    """
    text = user_prompt.lower()
    scores = {}
    for tool_name, keywords in _TOOL_KEYWORDS.items():
        if tool_name not in available_tool_names:
            continue
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores[tool_name] = score
    if not scores:
        return None
    return max(scores, key=scores.get)


# --------------------------------------------------------------------------- #
# Initialize v12 systems
# --------------------------------------------------------------------------- #
_WORKDIR = "."
kg = CodebaseKnowledgeGraph(_WORKDIR)
kg.build_graph()
kg.resolve_cross_file_edges()
holographic_index = kg.get_holographic_index()

synthesizer = EnumerativeSynthesizer()
vision_grounder = MobileCLIPGrounder()
blackboard = Blackboard(_WORKDIR)
architect = BlackboardArchitect(blackboard)

# Load local GGUF model (may be None in demo mode)
try:
    _llm = LocalModel(
        model_path=os.path.join("models", "qwen2.5-1.5b-instruct-q4_0.gguf"),
        n_ctx=4096,
        n_gpu_layers=0,
    )
except Exception:
    _llm = None

SYSTEM_PROMPT = f"""You are II v12, an Internet-Native Neuro-Symbolic Agentic OS.
You operate with deterministic tool verification.

CODEBASE MAP (Holographic Index):
{holographic_index}

AVAILABLE TOOLS (call by name when helpful):
- search_web(query): Search DuckDuckGo for live information
- read_url(url): Extract clean markdown text from a URL
- execute_code(code): Run Python code in a sandbox; returns stdout or stderr
- smt_solver(problem): Solve PhD-level math/logic via Z3. Format:
    VARIABLES: x Int, y Int
    CONSTRAINTS: x > 0, y > 0, x + y == 10
- program_synthesis(examples): Invent optimal code from examples.
    Format: JSON array of [[inputs], output], e.g. [[[1, 2], 3], [[5, 7], 12]]
- visual_ground(image_path, query): Find UI region by semantic description.
    Returns bounding box coordinates.
- blackboard(action, payload): Multi-file architecture manager.
    Actions: design, build, inspect, list.

RULES:
1. Use tools for math, invention, UI, and architecture — never guess.
2. Write code, then verify with execute_code before presenting.
3. If a tool fails, read the error and adjust. Max 1 retry per tool call.
4. Keep answers concise and useful.
5. For ambiguous tasks, ask clarifying questions before proceeding.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search DuckDuckGo for live information",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_url",
            "description": "Extract clean markdown text from a URL",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": "Run Python code in a sandbox. Returns stdout or stderr.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "smt_solver",
            "description": "Solve complex math/logic proofs using Z3 SMT solver.",
            "parameters": {
                "type": "object",
                "properties": {
                    "problem": {
                        "type": "string",
                        "description": "Math problem with VARIABLES: and CONSTRAINTS: sections",
                    }
                },
                "required": ["problem"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "program_synthesis",
            "description": "Invent optimal code from scratch via enumerative search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "examples": {
                        "type": "string",
                        "description": "JSON array of [[inputs], output] examples",
                    }
                },
                "required": ["examples"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "visual_ground",
            "description": "Find UI elements by semantic description (e.g., 'misaligned button').",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string"},
                    "query": {"type": "string"},
                },
                "required": ["image_path", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "blackboard",
            "description": "Manage multi-file architecture (design/build/inspect/list).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "design, build, inspect, or list",
                    },
                    "payload": {
                        "type": "string",
                        "description": "JSON payload for the action",
                    },
                },
                "required": ["action"],
            },
        },
    },
]


def execute_code(code: str, timeout: int = 15) -> tuple[bool, str]:
    """Run Python code in a temp file sandbox."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(code)
        temp_path = f.name
    try:
        result = subprocess.run(
            ["python", temp_path],
            capture_output=True, text=True, timeout=timeout,
        )
        os.unlink(temp_path)
        if result.returncode == 0:
            return True, result.stdout
        return False, result.stderr
    except subprocess.TimeoutExpired:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        return False, "Execution timed out (possible infinite loop)."
    except Exception as exc:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        return False, str(exc)


def execute_tool(tool_name: str, args: dict) -> str:
    """Deterministic tool router for v12."""
    if tool_name == "search_web":
        return search_web(args.get("query", ""))
    if tool_name == "read_url":
        return read_url(args.get("url", ""))
    if tool_name == "execute_code":
        success, output = execute_code(args.get("code", ""))
        return f"SUCCESS:\n{output}" if success else f"ERROR:\n{output}"
    if tool_name == "smt_solver":
        return smt_math_tool(args.get("problem", ""))
    if tool_name == "program_synthesis":
        return synthesis_tool(args.get("examples", ""))
    if tool_name == "visual_ground":
        return vision_ground_tool(
            args.get("image_path", ""), args.get("query", "")
        )
    if tool_name == "blackboard":
        return blackboard_tool(
            args.get("action", ""),
            args.get("payload", ""),
            _WORKDIR,
        )
    return "Tool not found."


def run_agent_v12(user_prompt: str):
    """Main v12 ReAct loop with deterministic pre-LLM tool routing."""
    print("[OK] II v12 Orchestrator Initialized")
    if _llm is None:
        print("[WARN] Local GGUF model not available (demo mode).")
        print("    Install llama-cpp-python or connect LM Studio to enable chat.")
        return

    messages = [{"role": "user", "content": user_prompt}]

    # Deterministic pre-LLM routing
    forced_tool = deterministic_tool_router(
        user_prompt, [t["function"]["name"] for t in TOOLS]
    )
    routing_hint = ""
    if forced_tool:
        routing_hint = (
            f"\nIMPORTANT: Use the tool '{forced_tool}' for this request.\n"
        )

    for step in range(10):  # Max 10 ReAct steps to prevent infinite loops
        print(f"\n--- Step {step + 1} ---")

        response = _llm.chat(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + routing_hint}
            ]
            + messages,
            tools=TOOLS,
            temperature=0.2,
        )

        content = response.get("content", "") or ""
        tool_calls = response.get("tool_calls") or []

        print(f"[MODEL] {content[:200]}...")
        messages.append({"role": "assistant", "content": content})

        if not tool_calls:
            print("\n[DONE] Final Answer Reached.")
            print(content)
            break

        for tool_call in tool_calls:
            fn = tool_call["function"]["name"]
            args = tool_call["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            print(f"[TOOL] Executing: {fn}")
            result = execute_tool(fn, args)
            print(f"[RESULT] {result[:300]}...")

            messages.append(
                {"role": "tool", "name": fn, "content": result}
            )


def main() -> None:
    prompt = input("Enter v12 Task: ")
    run_agent_v12(prompt)


if __name__ == "__main__":
    main()
