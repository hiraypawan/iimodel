"""
II v12 agent — with Internet-as-Brain research pipeline.

Adds:
  - Multi-backend web search (Tavily > SearXNG > DuckDuckGo)
  - Citation enforcement ([1], [2], etc.)
  - Multi-step research loop (search → fetch → verify → synthesize)
  - Self-verification before output (claims checked against cited sources)

All web lookups are explicitly marked as unverified retrieval; the model must
cite sources and the system flags unverified claims.
"""

from __future__ import annotations

import json
import tempfile
import os
import subprocess
import sys
import re
from typing import Optional

from ii_graph import CodebaseKnowledgeGraph
from graph.topology import GraphTopologyAnalyzer
from reasoning.verification_cascade import VerificationCascade
from ii_web import (
    search_web,
    read_url,
    fetch_url_metadata,
    format_source_tag,
    ResearchEngine,
    research_and_answer,
    verify_answer_citations,
)
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
        "information on", "details on", "duckduckgo", "tavily", "searxng"
    ),
    "read_url": (
        "http://", "https://", "fetch", "read url", "extract",
        "trafilatura", "webpage"
    ),
    "research": (
        "research", "investigate", "deep dive", "fact-check", "verify claim",
        "source", "cite", "evidence", "bibliography", "references"
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
research_engine = ResearchEngine()

# Load local GGUF model (may be None in demo mode)
try:
    _llm = LocalModel(
        model_path=os.path.join("models", "qwen2.5-1.5b-instruct-q4_0.gguf"),
        n_ctx=4096,
        n_gpu_layers=0,
    )
except Exception:
    _llm = None

# --------------------------------------------------------------------------- #
# System prompt — with citation enforcement & research guidance
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = f"""You are II, a coding assistant connected to an optional local model.
You have web-search and research tools. When answering questions that require
current or external information, you MUST use the research tools and cite sources.

REPOSITORY INDEX (approximate, may omit symbols):
{repository_index}

APPROXIMATE GRAPH TOPOLOGY (navigation aid, not architecture proof):
{architecture_map}

AVAILABLE TOOLS (call by name when helpful):
- search_web(query): Search DuckDuckGo/Tavily/SearXNG for current information.
  Returns results with citation IDs like [1], [2]. You MUST cite these.
- read_url(url): Fetch and extract clean text from a URL.
- research(query): Run a full research pipeline (search + fetch + evidence
  extraction + conflict detection). Returns sources, passages, and conflicts.
- execute_code(code): Run Python as a local subprocess; not isolated.
- smt_solver(problem): Solve explicit constraints with Z3 when installed.
  Format: VARIABLES: x Int, y Int / CONSTRAINTS: x > 0, y > 0, x + y == 10
- program_synthesis(examples): Search a bounded expression set. Format:
  JSON array of [[inputs], output], e.g. [[[1, 2], 3], [[5, 7], 12]]
- blackboard(action, payload): Experimental interface store.

CITATION RULES (MANDATORY for web-derived answers):
  1. Every factual claim from the web MUST carry a citation [1], [2], etc.
  2. Citation IDs come from the search results provided in tool output.
  3. Do not invent citation IDs that were not returned by tools.
  4. If sources conflict, state the conflict and cite both sides.
  5. If no source supports a claim, say "insufficient evidence".
  6. Before finalizing, re-read your answer and verify every [n] maps to a
     real source returned by the search tool.

PIPELINE STATUS:
- Repository graph summaries are available before inference.
- Web search returns citation-tagged results; verification is the model's job.
- No hidden-state hooks, forkable KV caches, or constrained-decoding interfaces.
- Static Python preflight is not a full correctness or safety proof.

GUIDANCE:
1. Verify important claims; tools do not guarantee correctness.
2. Treat retrieval passages as unverified excerpts. Lexical conflict flags are
   review prompts only. Say "insufficient evidence" when sources do not
   support a response.
3. Use the solver only for explicitly encoded constraints.
4. Treat code execution as potentially unsafe.
5. Keep answers concise and useful; ask clarifying questions when needed.
6. ALWAYS cite web sources. Uncited web claims will be rejected.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web (Tavily > SearXNG > DuckDuckGo). Returns citation-tagged results.",
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
            "description": "Fetch a URL and extract readable text with evidence passages.",
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
            "name": "research",
            "description": "Run a full research pipeline: search + fetch + extract evidence + detect conflicts. Returns sources, passages, and conflicts with citation IDs.",
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
            "description": "Solve explicit logical constraints with Z3 when installed. Format: VARIABLES: x Int, y Int / CONSTRAINTS: x > 0, y > 0, x + y == 10",
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
                        "description": "inspect, list, or graph",
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


# --------------------------------------------------------------------------- #
# Code execution
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Citation verification helper
# --------------------------------------------------------------------------- #
def _extract_citations(text: str) -> list[str]:
    """Find all [n] citation markers in text."""
    return re.findall(r"\[(\d+)\]", text)


def _verify_citations(text: str, available_citations: dict[str, dict]) -> dict:
    """
    Check that every [n] in text maps to a real citation ID.
    Returns {"ok": bool, "missing": [ids], "unused": [ids]}.
    """
    used = set(_extract_citations(text))
    available = set(available_citations.keys())
    missing = sorted(used - available, key=lambda x: int(x.strip("[]")))
    unused = sorted(available - used, key=lambda x: int(x.strip("[]")))
    return {
        "ok": len(missing) == 0,
        "missing": missing,
        "unused": unused,
    }


# --------------------------------------------------------------------------- #
# Tool dispatch
# --------------------------------------------------------------------------- #
def execute_tool(
    tool_name: str,
    args: dict,
    evidence_cache: list[dict[str, str]] | None = None,
) -> str:
    """Dispatch a selected tool to its implementation."""
    if tool_name == "search_web":
        return search_web(args.get("query", ""))
    if tool_name == "read_url":
        evidence = fetch_url_metadata(args.get("url", ""))
        if evidence_cache is not None:
            evidence_cache.extend(evidence.evidence_passages)
        return json.dumps(evidence.__dict__, ensure_ascii=False, default=str)
    if tool_name == "research":
        result = research_and_answer(args.get("query", ""))
        if evidence_cache is not None:
            evidence_cache.extend(result.get("passages", []))
        return json.dumps(result, ensure_ascii=False, default=str)
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


# --------------------------------------------------------------------------- #
# Self-verification wrapper: cite-check before finalizing
# --------------------------------------------------------------------------- #
def verify_answer_citations(answer_text: str,
                            sources: dict[str, dict]) -> dict:
    """
    Check that every citation in answer_text maps to a real source.
    Returns {"verified": bool, "missing": [...], "unused": [...], "answer": str}.
    """
    check = _verify_citations(answer_text, sources)
    return {
        "verified": check["ok"],
        "missing_citations": check["missing"],
        "unused_sources": check["unused"],
        "answer": answer_text,
    }


# --------------------------------------------------------------------------- #
# Research loop with self-verification
# --------------------------------------------------------------------------- #
def run_agent_v12(user_prompt: str):
    """Run a bounded tool-use loop with citation enforcement & self-verification."""
    print("[INFO] II v12 — Internet-as-Brain agent")
    if _llm is None or getattr(_llm, "mode", "demo") == "demo":
        print("[WARN] No language-model backend is connected; demo mode cannot answer prompts.")
        print("    Recommended: load a compatible model in LM Studio and start its local server.")
        print("    Alternative: provide a GGUF file and install llama-cpp-python.")
        return

    messages = [{"role": "user", "content": user_prompt}]
    evidence_cache: list[dict[str, str]] = []

    # Optional keyword heuristic
    hinted_tool = keyword_tool_hint(
        user_prompt, [t["function"]["name"] for t in TOOLS]
    )
    routing_hint = ""
    if hinted_tool:
        routing_hint = (
            f"\nA keyword heuristic suggests '{hinted_tool}' may be relevant; "
            "use it only if appropriate.\n"
        )

    research_results: dict | None = None

    for step in range(10):  # Max 10 ReAct steps
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
        messages.append(response)

        if not tool_calls:
            # Self-verification: check citations before finalizing
            print("\n[VERIFY] Checking citations against sources...")
            verify_result = verify_answer_citations(
                content, research_results.get("sources", {}) if research_results else {}
            )
            if not verify_result["verified"]:
                print(f"[VERIFY] MISSING citations: {verify_result['missing_citations']}")
                print("[VERIFY] Asking model to fix citations...")
                fix_prompt = (
                    f"Your answer has missing or invalid citations: {verify_result['missing_citations']}.\n"
                    f"Available sources: {json.dumps(verify_result['unused_sources'] or {}, ensure_ascii=False)}\n"
                    f"Please rewrite your answer with correct citations [1], [2], etc. "
                    f"Only cite sources that were returned by the search tool.\n"
                    f"Original answer:\n{content}"
                )
                messages.append({"role": "user", "content": fix_prompt})
                continue  # retry with fix prompt

            print("\n[DONE] Final Answer Reached (citations verified).")
            print(content)
            break

        # Handle tool calls
        for tool_call in tool_calls:
            fn = tool_call["function"]["name"]
            args = tool_call["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            print(f"[TOOL] Executing: {fn}")
            result = execute_tool(fn, args, evidence_cache=evidence_cache)
            print(f"[RESULT] {result[:300]}...")

            # Cache research results for citation verification later
            if fn == "research":
                try:
                    research_results = json.loads(result)
                except json.JSONDecodeError:
                    pass

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
