"""
II command-line agent prototype.

This module combines a local model backend with optional helper tools. Its
keyword-based tool hint is a lightweight heuristic, not a guarantee that the
model will call the right tool. Some tools are prototypes.

Web search and URL fetching make external network requests; Python execution is
performed in the current user's environment and is not isolated.
"""

from __future__ import annotations

import json
import tempfile
import os
import subprocess
import sys

from ii_graph import CodebaseKnowledgeGraph
from graph.topology import GraphTopologyAnalyzer
from reasoning.verification_cascade import VerificationCascade
from ii_web import search_web, fetch_evidence, candidate_contradictions
from ii_smt import smt_math_tool
from ii_synthesis import EnumerativeSynthesizer, synthesis_tool
from ii_blackboard import (
    Blackboard,
    BlackboardArchitect,
    BlackboardBuilder,
    BlackboardInspector,
    blackboard_tool,
)
from llama_local import LocalModel

# --------------------------------------------------------------------------- #
# Lightweight keyword-based tool hint (heuristic only)
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

def keyword_tool_hint(user_prompt: str, available_tool_names: list) -> str | None:
    """Return the highest-scoring keyword match as a non-binding hint."""
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
repository_index = kg.get_repository_index()
architecture_map = GraphTopologyAnalyzer(kg.graph).generate_architecture_map(max_tokens=600)

synthesizer = EnumerativeSynthesizer()
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

SYSTEM_PROMPT = f"""You are II, a coding assistant connected to an optional local model.
The repository index is approximate and may omit symbols:
{repository_index}

Approximate graph topology (navigation aid, not architecture proof):
{architecture_map}

Pipeline status: repository graph summaries are available before inference; the
current backends do not expose portable hidden-state hooks, forkable KV caches,
or constrained-decoding interfaces. Static Python preflight is not a full
three-gate correctness or safety proof.

Available helpers:
- search_web(query): sends a query to an external web-search service
- read_url(url): returns source URLs and exact extracted passages for review; extraction is not fact-checking
- execute_code(code): runs a Python subprocess with the current user's permissions; it is not isolated
- smt_solver(problem): solves explicit constraints when Z3 is installed. Format:
    VARIABLES: x Int, y Int
    CONSTRAINTS: x > 0, y > 0, x + y == 10
- program_synthesis(examples): searches a bounded expression set. Format:
    JSON array of [[inputs], output], e.g. [[[1, 2], 3], [[5, 7], 12]]
- blackboard(action, payload): supports listing, inspection, and an approximate repository graph

Guidance:
1. Verify important claims; tools do not guarantee correctness.
2. Treat retrieval passages as unverified excerpts; lexical conflict flags are only review prompts. Say “insufficient evidence” when sources do not support a response.
3. Use the solver only for explicitly encoded constraints.
4. Treat code execution as potentially unsafe.
5. Keep answers concise and useful; ask clarifying questions when needed.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for current information; sends a request to an external service.",
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
            "description": "Fetch a URL and extract readable text",
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
            "description": "Run Python code as a subprocess using the current user's permissions; not isolated.",
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
            "description": "Solve explicit logical constraints with Z3 when installed; does not parse arbitrary natural language.",
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
            "description": "Search a small bounded expression set for a program matching examples.",
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
            "name": "blackboard",
            "description": "Experimental interface store; supports inspect, list, and approximate graph actions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "inspect, list, or approximate graph",
                        "enum": ["inspect", "list", "graph"],
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
    """Run Python in a temporary subprocess (not an OS-level sandbox)."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(code)
        temp_path = f.name
    try:
        result = subprocess.run(
            [sys.executable, temp_path],
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


def execute_tool(
    tool_name: str,
    args: dict,
    evidence_cache: list[dict[str, str]] | None = None,
) -> str:
    """Dispatch a selected tool to its implementation."""
    if tool_name == "search_web":
        return search_web(args.get("query", ""))
    if tool_name == "read_url":
        evidence = fetch_evidence(args.get("url", ""))
        if evidence_cache is not None:
            evidence_cache.extend(evidence.get("evidence_passages", []))
            evidence["possible_conflicts"] = candidate_contradictions(evidence_cache)
        return json.dumps(evidence, ensure_ascii=False)
    if tool_name == "execute_code":
        success, output = execute_code(args.get("code", ""))
        return f"SUCCESS:\n{output}" if success else f"ERROR:\n{output}"
    if tool_name == "smt_solver":
        return smt_math_tool(args.get("problem", ""))
    if tool_name == "program_synthesis":
        return synthesis_tool(args.get("examples", ""))
    if tool_name == "blackboard":
        return blackboard_tool(
            args.get("action", ""),
            args.get("payload", ""),
            _WORKDIR,
        )
    return "Tool not found."


def run_agent_v12(user_prompt: str):
    """Run a bounded tool-use loop with optional keyword hints."""
    print("[INFO] II local-agent prototype")
    if _llm is None or getattr(_llm, "mode", "demo") == "demo":
        print("[WARN] No language-model backend is connected; demo mode cannot answer prompts.")
        print("    Recommended: load a compatible model in LM Studio and start its local server.")
        print("    Alternative: provide a GGUF file and install llama-cpp-python.")
        return

    messages = [{"role": "user", "content": user_prompt}]
    evidence_cache: list[dict[str, str]] = []

    # Optional keyword heuristic. The model may ignore this suggestion.
    hinted_tool = keyword_tool_hint(
        user_prompt, [t["function"]["name"] for t in TOOLS]
    )
    routing_hint = ""
    if hinted_tool:
        routing_hint = (
            f"\nA keyword heuristic suggests '{hinted_tool}' may be relevant; "
            "use it only if appropriate.\n"
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
        # Preserve tool_calls in the assistant message for OpenAI-compatible APIs.
        messages.append(response)

        if not tool_calls:
            print("\n[DONE] Final Answer Reached.")
            print(content)
            if "```python" in content:
                code = content.split("```python", 1)[1].split("```", 1)[0]
                report = VerificationCascade().verify_python(code)
                print("[STATIC PREFLIGHT ONLY]")
                print(json.dumps(report.to_dict(), indent=2))
            break

        for tool_call in tool_calls:
            fn = tool_call["function"]["name"]
            args = tool_call["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            print(f"[TOOL] Executing: {fn}")
            result = execute_tool(fn, args, evidence_cache=evidence_cache)
            print(f"[RESULT] {result[:300]}...")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id"),
                    "name": fn,
                    "content": result,
                }
            )


def main() -> None:
    prompt = input("Enter v12 Task: ")
    run_agent_v12(prompt)


if __name__ == "__main__":
    main()
