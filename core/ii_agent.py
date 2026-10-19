"""
II v3.0 legacy agent — the model-driven tool-calling loop.

Adds:
  - Multi-backend web search (Brave > SearXNG > DuckDuckGo > GitHub)
  - Citation enforcement ([1], [2], etc.)
  - Multi-step research loop (search → fetch → verify → synthesize)
  - Self-verification before output (claims checked against cited sources)

All web lookups are explicitly marked as unverified retrieval; the model must
cite sources and the system flags unverified claims.
"""

from __future__ import annotations

import json
import os
import re

from graph.ii_graph import CodebaseKnowledgeGraph
from graph.topology import GraphTopologyAnalyzer
from ingestion.ii_web import (
    search_web,
    fetch_url_metadata,
    ResearchEngine,
    research_and_answer,
)
from reasoning.ii_smt import smt_math_tool
from reasoning.ii_synthesis import EnumerativeSynthesizer, synthesis_tool
from blackboard.ii_blackboard import (
    Blackboard,
    BlackboardArchitect,
    blackboard_tool,
)
from core.ii_security import is_private_mode, run_python_code, web_blocked_message

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
# Lazy initialisation
#
# The repository index and the model are built on first use, not at import
# time: importing the agent must stay cheap and inside the RAM budget.
# --------------------------------------------------------------------------- #
_WORKDIR = "."

kg: CodebaseKnowledgeGraph | None = None
repository_index: str = ""
architecture_map: str = ""

synthesizer: EnumerativeSynthesizer | None = None
blackboard: Blackboard | None = None
architect: BlackboardArchitect | None = None
research_engine: ResearchEngine | None = None

_llm = None
_LLM_LOADED = False


def _ensure_graph() -> None:
    """Build the repository index once (lazily)."""
    global kg, repository_index, architecture_map
    if kg is not None:
        return
    try:
        kg = CodebaseKnowledgeGraph(_WORKDIR)
        kg.build_graph()
        kg.resolve_cross_file_edges()
        repository_index = kg.get_repository_index()
        architecture_map = GraphTopologyAnalyzer(kg.graph).generate_architecture_map(
            max_tokens=600
        )
    except Exception:
        kg = None
        repository_index = ""
        architecture_map = ""


def _ensure_services() -> None:
    """Create the blackboard / research helpers once (lazily)."""
    global synthesizer, blackboard, architect, research_engine
    if synthesizer is None:
        synthesizer = EnumerativeSynthesizer()
    if blackboard is None:
        blackboard = Blackboard(_WORKDIR)
        architect = BlackboardArchitect(blackboard)
    if research_engine is None:
        research_engine = ResearchEngine()


def get_model():
    """Return the shared local-model backend (loads it on first use)."""
    global _llm, _LLM_LOADED
    if not _LLM_LOADED:
        _LLM_LOADED = True
        try:
            from core.llama_local import LocalModel

            _llm = LocalModel(
                model_path=os.path.join("models", "qwen2.5-1.5b-instruct-q4_0.gguf"),
                n_ctx=4096,
                n_gpu_layers=0,
            )
        except Exception:
            _llm = None
    return _llm

# --------------------------------------------------------------------------- #
# System prompt — with citation enforcement & research guidance
# --------------------------------------------------------------------------- #
def system_prompt() -> str:
    """Build the legacy agent system prompt (repository context included)."""
    _ensure_graph()
    return f"""You are II, a coding assistant connected to an optional local model.
You have web-search and research tools. When answering questions that require
current or external information, you MUST use the research tools and cite sources.

REPOSITORY INDEX (approximate, may omit symbols):
{repository_index}

APPROXIMATE GRAPH TOPOLOGY (navigation aid, not architecture proof):
{architecture_map}

AVAILABLE TOOLS (call by name when helpful):
- search_web(query): Search Brave/SearXNG/DuckDuckGo/GitHub for current information.
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
            "description": "Search configured web providers concurrently and return citation-tagged results.",
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
    """Run Python via ii_security.run_python_code (see its docstring).

    Default subprocess mode is NOT a sandbox; II_CODE_SANDBOX=docker gives
    fail-closed Docker isolation.
    """
    return run_python_code(code, timeout=timeout)


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
    if tool_name in {"search_web", "read_url", "research"} and is_private_mode():
        return web_blocked_message(tool_name)
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
def run_agent(user_prompt: str):
    """Run a bounded tool-use loop with citation enforcement & self-verification."""
    print("[INFO] II v3.0 -- legacy tool-calling agent")
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
                {"role": "system", "content": system_prompt() + routing_hint}
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


# Backward-compatible alias (pre-v2.0 name).
run_agent_v12 = run_agent


def main() -> None:
    prompt = input("Enter II Task: ")
    run_agent(prompt)


if __name__ == "__main__":
    main()


# --------------------------------------------------------------------------- #
# PURE II ROUTER
#
# The router decides *which* deterministic pipeline runs.  Pure II renders the
# resulting structured data in Python and has no language-model dependency.
# --------------------------------------------------------------------------- #

#: Intent rules.  Each family lists explicit surface markers; the score is
#: the number of distinct markers that match.
_INTENT_MARKERS: dict[str, tuple[str, ...]] = {
    "empathy": (
        "frustrat", "annoy", "anxious", "anxiety", "worried", "stressed",
        "overwhelmed", "depressed", "lonely", "i feel", "i'm feeling",
        "upset", "angry", "disappointed", "burned out", "burnt out",
        "exhausted", "can you help me cope", "emotional",
    ),
    "brainstorm": (
        "brainstorm", "give me ideas", "ideas about", "suggest", "name some",
        "creative", "concepts for", "what could i build", "ideate",
    ),
    "hypothesis": (
        "hypothesis", "hypotheses", "invent", "novel approach", "what if",
        "combine", "cross-pollinate", "unusual way",
    ),
    "code": (
        "write a function", "write a script", "write code", "implement",
        "build a", "create a program", "debug", "fix this code", "refactor",
        "unit test", "automate", "write a tool",
    ),
    "research": (
        "research", "compare", "investigate", "find out", "latest",
        "current news", "market", "state of", "report on", "summary of",
        "what happened", "who won",
    ),
    "chat": (
        "hello", "hi", "hii", "hi there", "hey", "namaste",
        "good morning", "good afternoon", "good evening", "thanks",
        "thank you", "who are you", "what are you", "what can you do",
        "how are you", "help", "bye", "goodbye",
    ),
    # Attribute / leadership / creator / comparison style questions.  These
    # are the shapes the logic-tree solver decomposes best, so they carry
    # explicit evidence of the default intent instead of falling through to
    # it with zero confidence.
    "logic_tree": (
        "who is", "who was", "who founded", "who created", "who invented",
        "when did", "when was", "where is", "where did", "how many",
        "how much", "capital of", "founder of", "ceo of", "creator of",
        "created by", "invented by", "headquarters of", "population of",
        "born in", "died in",
    ),
}

#: Multi-step markers (used by the interviewer and the fast-path check).
_MULTI_STEP_MARKERS = (
    " and then ", " then ", " after that ", " followed by ", " first ",
    " secondly ", " thirdly ", " finally ", " next ", " step 1", " step 2",
    " additionally ", " also ",
)

#: Relational indicators that make a question genuinely multi-hop.
_HOP_INDICATOR_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in (
        r"\b(?:CEO|founder|owner|president|creator)\s+of\b",
        r"\bwho\s+(?:made|created|founded|invented|owns)\b",
        r"\b(?:that|which)\s+\w+\s+(?:made|created|founded|invented|owns)\b",
        r"\bcompared\s+(?:to|with)\b",
        r"\b\w+er\s+than\b",
        r"\b(?:taller|older|richer|bigger|larger)\s+than\b",
        r"\bafter\s+the\s+\w+\s+that\b",
    )
)

_FACTUAL_QUESTION_RE = re.compile(
    r"^(who|what|when|where|which|how\s+(?:much|many|long|old|tall|far|big)|"
    r"is|are|was|were|does|did|do|can|name\s+the|list|tell\s+me\s+about)\b",
    re.IGNORECASE,
)

# Short conversational messages must never be sent to the web-search solver.
# Besides being semantically wrong, doing that made a greeting wait for every
# network timeout before returning ``INSUFFICIENT_DATA``.  These patterns are
# deliberately anchored: "hi, who is ...?" still reaches the factual pipeline.
_DIRECT_CHAT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("greeting", re.compile(
        r"^(?:(?:h+i+|he+l+o+|hey+|yo+|namaste)(?:\s+(?:ii|there))?|"
        r"good\s+(?:morning|afternoon|evening))(?:\s*[!.?]*)$",
        re.IGNORECASE,
    )),
    ("thanks", re.compile(
        r"^(?:thanks|thank\s+you|thx|ty)(?:\s+(?:ii|so\s+much))?[!.?]*$",
        re.IGNORECASE,
    )),
    ("identity", re.compile(
        r"^(?:who|what)\s+are\s+you[!.?]*$",
        re.IGNORECASE,
    )),
    ("capabilities", re.compile(
        r"^(?:(?:what|which)\s+can\s+you\s+do|help|help\s+me|"
        r"show\s+me\s+what\s+you\s+can\s+do)[!.?]*$",
        re.IGNORECASE,
    )),
    ("wellbeing", re.compile(
        r"^(?:how\s+are\s+you|how(?:'|’)s\s+it\s+going)[!.?]*$",
        re.IGNORECASE,
    )),
    ("farewell", re.compile(
        r"^(?:bye+|goodbye|see\s+you|later)[!.?]*$",
        re.IGNORECASE,
    )),
)

_DIRECT_CHAT_RESPONSES = {
    "greeting": (
        "Hi! I'm II. I use deterministic reasoning, internet sources, and "
        "local tools rather than a GGUF model. Ask me a factual question, "
        "request research, solve constraints, or give me code to verify."
    ),
    "thanks": "You're welcome. What would you like to work on next?",
    "identity": (
        "I'm II — Pure Internet Intelligence. My answers are produced by "
        "deterministic pipelines that search sources, run tools, and show "
        "their evidence; no GGUF model is required."
    ),
    "capabilities": (
        "I can research live information with citations, solve multi-step "
        "factual questions, inspect repositories, check constraints with Z3, "
        "run bounded program synthesis, and verify code. Try asking a direct "
        "question or describing the task you want completed."
    ),
    "wellbeing": "Ready to work. What should I research, solve, or verify?",
    "farewell": "Goodbye!",
}


def direct_chat_kind(query: str) -> str | None:
    """Classify a self-contained conversational message without web access."""
    text = " ".join((query or "").strip().split())
    for kind, pattern in _DIRECT_CHAT_PATTERNS:
        if pattern.fullmatch(text):
            return kind
    return None


def direct_chat_payload(query: str) -> dict | None:
    """Return a deterministic response for greetings/help, otherwise ``None``."""
    kind = direct_chat_kind(query)
    if kind is None:
        return None
    return {
        "status": "SUPPORTIVE_INFORMATION",
        "message": _DIRECT_CHAT_RESPONSES[kind],
        "response_kind": kind,
        "sources": [],
    }


_exocortex_instance = None


def get_exocortex():
    """Return the shared Exocortex instance (disk-backed paged memory)."""
    global _exocortex_instance
    if _exocortex_instance is None:
        from memory.exocortex import Exocortex

        _exocortex_instance = Exocortex()
    return _exocortex_instance


def count_hop_indicators(query: str) -> int:
    """Count the relational indicators in ``query`` (distinct patterns)."""
    text = query or ""
    return sum(1 for pattern in _HOP_INDICATOR_PATTERNS if pattern.search(text))


def is_complex_logical_query(query: str) -> bool:
    """Concrete complexity test: >=2 hop indicators OR >=3 named entities."""
    from core.nlp import extract_entities

    if count_hop_indicators(query) >= 2:
        return True
    return len(extract_entities(query or "")) >= 3


def is_simple_factual(query: str) -> bool:
    """True for a short, single-step factual question (fast path)."""
    text = (query or "").strip()
    if not text or len(text.split()) > 25:
        return False
    if not _FACTUAL_QUESTION_RE.match(text):
        return False
    lowered = f" {text.lower()} "
    if any(marker in lowered for marker in _MULTI_STEP_MARKERS):
        return False
    if is_complex_logical_query(text):
        return False
    return True


def classify_intent(query: str) -> tuple[str, float]:
    """Deterministic intent classification.

    Returns ``(intent, confidence)``.  The confidence is derived from the
    number of matching markers, never from a model.
    """
    if direct_chat_kind(query) is not None:
        return "chat", 1.0

    text = (query or "").lower()
    scores: dict[str, int] = {}
    for intent, markers in _INTENT_MARKERS.items():
        hits = 0
        for marker in markers:
            # Word-boundary matching: "capital" must not match the marker
            # "api", and "cod" must not match "code".
            if re.search(rf"\b{re.escape(marker)}\b", text):
                hits += 1
        if hits:
            scores[intent] = hits
    if not scores:
        return "logic_tree", 0.0
    intent = max(scores, key=lambda key: (scores[key], key == "logic_tree"))
    confidence = min(1.0, scores[intent] / 2.0)
    return intent, round(confidence, 3)


def _pipeline_payload(status: str, **extra) -> dict:
    payload = {"status": status}
    payload.update(extra)
    return payload


def _render(payload: dict, llm=None) -> str:
    from core.caged_renderer import CagedRenderer

    renderer = CagedRenderer(llm=llm)
    return renderer.format(payload)


def _build_node_functions(intent: str, prompt: str, context: dict, llm) -> dict:
    """Bind the real work to the planner's node ids."""
    from interaction.ii_empathy_module import EmpathyModule
    from interaction.ii_brainstorming_engine import BrainstormingEngine
    from reasoning.logic_tree_solver import LogicTreeSolver

    solver = LogicTreeSolver()
    functions: dict[str, callable] = {}

    if intent == "logic_tree":
        def solve(shared: dict) -> dict:
            return {"raw": solver.solve(prompt)}
        functions["solve"] = solve

    elif intent == "research":
        def web_fetch(shared: dict) -> dict:
            return {"web": solver.solve(prompt)}
        def memory_recall(shared: dict) -> dict:
            if not context:
                return {"memory": [], "memory_note": "memory not used (no opt-in)"}
            exocortex = get_exocortex()
            return {
                "memory": exocortex.recall(prompt, top_k=5),
                "memory_note": "recalled from the Exocortex (opt-in memory)",
            }
        def merge(shared: dict) -> dict:
            raw = shared.get("web", {})
            raw = dict(raw)
            raw["memory"] = shared.get("memory", [])
            raw["memory_note"] = shared.get("memory_note", "")
            return {"raw": raw}
        functions["web_fetch"] = web_fetch
        functions["memory_recall"] = memory_recall
        functions["merge"] = merge

    elif intent == "code":
        from core.critic_loop import CriticLoop
        from interaction.interviewer import Interviewer
        from tools.self_tool_builder import SelfToolBuilder

        builder = SelfToolBuilder(llm=llm)
        critic = CriticLoop(llm=llm)
        tree = Interviewer().build_constraint_tree(prompt, context)

        def spec(shared: dict) -> dict:
            return {"constraint_tree": tree}
        def generate(shared: dict) -> dict:
            result = builder.build(prompt, shared.get("constraint_tree"))
            return {"generated": result}
        def sandbox_run(shared: dict) -> dict:
            generated = shared.get("generated", {})
            code = generated.get("code", "")
            ok, output = builder.sandbox_run(code)
            return {"sandbox": {"ok": ok, "output": output[:2000]}}
        def critic_node(shared: dict) -> dict:
            generated = shared.get("generated", {})
            review = critic.review(
                generated.get("code", ""),
                shared.get("constraint_tree"),
                is_web_ui=_looks_like_web_ui(prompt),
            )
            return {"review": review}
        functions["spec"] = spec
        functions["generate"] = generate
        functions["sandbox_run"] = sandbox_run
        functions["critic"] = critic_node

    elif intent == "empathy":
        module = EmpathyModule(llm=llm)

        def crisis_scan(shared: dict) -> dict:
            return {"crisis": module.detect_crisis(prompt)}
        def sentiment(shared: dict) -> dict:
            return {"buckets": module.sentiment_buckets(prompt)}
        def frameworks(shared: dict) -> dict:
            if shared.get("crisis"):
                return {"raw": module.crisis_response()}
            return {"raw": module.support_plan(prompt)}
        functions["crisis_scan"] = crisis_scan
        functions["sentiment"] = sentiment
        functions["frameworks"] = frameworks

    elif intent == "brainstorm":
        engine = BrainstormingEngine(llm=llm)

        def trends(shared: dict) -> dict:
            return {"trends": engine.fetch_trends(prompt)}
        def scamper(shared: dict) -> dict:
            return {"raw": engine.generate(prompt)}
        functions["trends"] = trends
        functions["scamper"] = scamper

    elif intent == "hypothesis":
        from invention.combinatorial_hypothesis_engine import (
            CombinatorialHypothesisEngine,
        )

        engine = CombinatorialHypothesisEngine(llm=llm)

        def domain_a(shared: dict) -> dict:
            return {"domain_a": engine.fetcher(
                f"biological mechanisms related to {prompt}", 5)}
        def domain_b(shared: dict) -> dict:
            return {"domain_b": engine.fetcher(
                f"engineering materials for {prompt}", 5)}
        def combine(shared: dict) -> dict:
            return {"combos": engine.generate_combinations(
                shared.get("domain_a", []), shared.get("domain_b", []))}
        def baseline(shared: dict) -> dict:
            return {"baseline": engine.simulate_baseline(prompt)}
        def simulate(shared: dict) -> dict:
            combos = shared.get("combos", [])
            if not combos:
                return {"raw": {
                    "status": "INSUFFICIENT_DATA",
                    "message": "No hypotheses passed plausibility screening.",
                    "assumptions": [],
                    "sources": [],
                }}
            return {"raw": engine.test_hypotheses(prompt)}
        functions["domain_a"] = domain_a
        functions["domain_b"] = domain_b
        functions["combine"] = combine
        functions["baseline"] = baseline
        functions["simulate"] = simulate

    else:  # chat
        def finalize(shared: dict) -> dict:
            payload = direct_chat_payload(prompt)
            if payload is None:
                payload = {
                    "status": "SUPPORTIVE_INFORMATION",
                    "message": (
                        "I can help through internet research and deterministic "
                        "tools. Please ask a factual question or describe a "
                        "research, constraint-solving, repository, or coding task."
                    ),
                    "response_kind": "guidance",
                    "sources": [],
                }
            return {"raw": payload}
        functions["finalize"] = finalize

    def finalize(shared: dict) -> dict:  # noqa: F811 - shared finaliser
        raw = shared.get("raw")
        if raw is None:
            raw = _pipeline_payload(
                "INSUFFICIENT_DATA",
                message="The pipeline produced no result.",
                sources=[],
            )
        return {"raw": raw}

    functions.setdefault("finalize", finalize)
    return functions


def _looks_like_web_ui(prompt: str) -> bool:
    lowered = (prompt or "").lower()
    return any(word in lowered for word in (
        "web ui", "html", "web page", "landing page", "frontend", "ui ",
        "browser", "website",
    ))


def route_task(user_prompt: str) -> dict:
    """Route a request through the Pure II pipeline.

    Order of operations (fixed):

    1. crisis scan,
    2. fast path for simple factual questions,
    3. memory context (only when the user opted in),
    4. interviewer (at most two questions, then defaults),
    5. deterministic intent classification,
    6. DAG execution,
    7. caged rendering.
    """
    from interaction.ii_empathy_module import EmpathyModule
    from interaction.interviewer import Interviewer
    from tools.self_tool_builder import matches_tool_task

    text = (user_prompt or "").strip()
    # Pure II is the reasoning engine.  The default pipeline intentionally has
    # no GGUF/LM Studio dependency; structured results are rendered by Python.
    llm = None

    if not text:
        payload = _pipeline_payload(
            "INSUFFICIENT_DATA",
            message="The request was empty, so nothing could be answered.",
            sources=[],
        )
        return {
            "status": payload["status"],
            "answer": _render(payload, llm),
            "raw": payload,
            "intent": "chat",
            "confidence": 0.0,
            "pipeline": "pure_ii",
            "questions": [],
            "event_log": [],
            "sources": [],
        }

    # 1. crisis first -- no other module runs.
    empathy = EmpathyModule(llm=llm)
    if empathy.detect_crisis(text):
        payload = empathy.crisis_response()
        return {
            "status": payload["status"],
            "answer": _render(payload, llm),
            "raw": payload,
            "intent": "empathy",
            "confidence": 1.0,
            "pipeline": "pure_ii:crisis",
            "questions": [],
            "event_log": [],
            "sources": payload.get("sources", []),
        }

    # Local conversation fast path.  Greetings and capability questions are
    # understood directly and never incur a network request.
    conversational = direct_chat_payload(text)
    if conversational is not None:
        return {
            "status": conversational["status"],
            "answer": conversational["message"],
            "raw": conversational,
            "intent": "chat",
            "confidence": 1.0,
            "pipeline": "pure_ii:chat_fast",
            "response_kind": conversational.get("response_kind"),
            "questions": [],
            "event_log": [],
            "sources": [],
        }

    # 2. fast path: simple factual question.
    if is_simple_factual(text):
        from reasoning.logic_tree_solver import LogicTreeSolver

        raw = LogicTreeSolver().solve(text)
        return {
            "status": raw.get("status", "INSUFFICIENT_DATA"),
            "answer": _render(raw, llm),
            "raw": raw,
            "intent": "logic_tree",
            "confidence": 1.0,
            "pipeline": "pure_ii:logic_tree_fast",
            "questions": [],
            "event_log": [],
            "sources": raw.get("sources", []),
        }

    # 3. memory context (opt-in only).
    exocortex = get_exocortex()
    context = exocortex.get_user_context()

    # 4. interviewer: at most two questions, then proceed with defaults.
    interviewer = Interviewer()
    questions: list[dict] = []
    if interviewer.needs_clarification(text, context):
        asked = interviewer.ask_questions(text, context)
        questions = asked.get("questions", [])

    # 5. deterministic intent classification.
    intent, confidence = classify_intent(text)
    used_self_tool = False
    if confidence < 0.5 and matches_tool_task(text):
        intent = "code"
        used_self_tool = True
    elif confidence < 0.5:
        # Default: research it, do not chat-guess.
        intent = "logic_tree"

    # 6. DAG execution.
    import asyncio

    from core.dag_planner import DAGPlanner

    planner = DAGPlanner()
    functions = _build_node_functions(intent, text, context, llm)
    graph = planner.create_execution_graph(intent, node_functions=functions)
    outcome = asyncio.run(planner.execute(graph))
    raw = outcome.get("results", {}).get("finalize", {}).get("raw")
    if raw is None:
        raw = _pipeline_payload(
            "INSUFFICIENT_DATA",
            message=(
                "The pipeline could not finish: "
                + (", ".join(outcome.get("errors", {}).values()) or "unknown error")
            ),
            sources=[],
        )

    # 7. write to the Exocortex (opt-in only, never on the fast path).
    if context:
        try:
            episode = exocortex.start_episode(f"pure_ii:{intent}:{text[:120]}")
            exocortex.end_episode(episode, f"{intent} completed")
        except Exception:
            pass

    return {
        "status": raw.get("status", "INSUFFICIENT_DATA"),
        "answer": _render(raw, llm),
        "raw": raw,
        "intent": intent,
        "confidence": confidence,
        "pipeline": f"pure_ii:dag:{intent}",
        "questions": questions,
        "constraint_tree": interviewer.build_constraint_tree(text, context),
        "event_log": outcome.get("event_log", []),
        "errors": outcome.get("errors", {}),
        "used_self_tool": used_self_tool,
        "response_kind": raw.get("response_kind") if isinstance(raw, dict) else None,
        "sources": raw.get("sources", []) if isinstance(raw, dict) else [],
    }
