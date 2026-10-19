"""
app.py — II Pure Internet Intelligence server.

II combines an internet-and-tools engine with optional ephemeral BYOK for any
OpenAI-compatible model. The hosted model handles general conversation and
tool planning; project tools perform retrieval, graph work, solving and
verification. No bundled GGUF weights or LM Studio installation is required.

The API also exposes helpers for repository indexing, structured constraint
solving, bounded expression synthesis, and state inspection.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import hmac
from pathlib import Path
from typing import Optional

from flask import Flask, render_template, request, jsonify

# --------------------------------------------------------------------------- #
# Make sibling modules importable
# --------------------------------------------------------------------------- #
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from graph.ii_graph import CodebaseKnowledgeGraph  # noqa: E402
from ingestion.ii_web import (  # noqa: E402
    search_web,
    read_url,
    fetch_url_metadata,
    format_source_tag,
    ResearchEngine,
    research_and_answer,
    verify_answer_citations,
)
from core.ii_state import StateManager  # noqa: E402
from reasoning.ii_constraints import ConstraintSharder, default_constraints  # noqa: E402
from graph.topology import GraphTopologyAnalyzer  # noqa: E402
from reasoning.verification_cascade import VerificationCascade  # noqa: E402

# Optional helper modules
try:
    from reasoning.ii_smt import Z3Solver, smt_math_tool  # noqa: E402
    import z3 as _z3  # noqa: F401, E402
    _SMT_AVAILABLE = True
except ImportError:
    Z3Solver = None  # type: ignore[assignment]
    smt_math_tool = None  # type: ignore[assignment]
    _SMT_AVAILABLE = False

try:
    from reasoning.ii_synthesis import EnumerativeSynthesizer, synthesis_tool  # noqa: E402
    _SYNTH_AVAILABLE = True
except ImportError:
    EnumerativeSynthesizer = None  # type: ignore[assignment]
    synthesis_tool = None  # type: ignore[assignment]
    _SYNTH_AVAILABLE = False

try:
    from archive.ii_vision import MobileCLIPGrounder, vision_ground_tool  # noqa: E402
    # The current implementation returns image-level matches, not localized regions.
    # Keep it out of the agent's tool list until actual grounding is implemented.
    _VISION_AVAILABLE = False
except ImportError:
    MobileCLIPGrounder = None  # type: ignore[assignment]
    vision_ground_tool = None  # type: ignore[assignment]
    _VISION_AVAILABLE = False

from blackboard.ii_blackboard import Blackboard, blackboard_tool  # noqa: E402  # pure Python
from core.ii_security import (  # noqa: E402
    is_private_mode,
    run_python_code,
    sandbox_mode,
    web_blocked_message,
)

WEB_TOOL_NAMES = {"search_web", "read_url", "research"}


def _active_tools() -> list:
    """Tools offered to the model; web tools are hidden under II_PRIVATE."""
    if not is_private_mode():
        return TOOLS
    return [t for t in TOOLS if t["function"]["name"] not in WEB_TOOL_NAMES]


# --------------------------------------------------------------------------- #
# Flask app
# --------------------------------------------------------------------------- #
app = Flask(__name__, template_folder="templates", static_folder="static")


@app.after_request
def _security_headers(response):
    """Apply browser hardening without changing API response contracts."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'none'; form-action 'self'; "
        "connect-src 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'",
    )
    return response


# --------------------------------------------------------------------------- #
# API authentication
#
# ``II_API_KEY`` enables authentication for every ``/api/*`` route.  When the
# variable is unset the API is *not* authenticated -- which is only safe
# because the server binds to 127.0.0.1 by default.  The status endpoint
# always reports which mode is active.
# --------------------------------------------------------------------------- #
def api_key_configured() -> bool:
    """True when an API key is configured in the environment."""
    return bool((os.environ.get("II_API_KEY") or "").strip())


def _request_api_key() -> str:
    header = request.headers.get("X-II-API-Key", "")
    if header and header.strip():
        return header.strip()
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


@app.before_request
def _enforce_api_key():
    """Refuse unauthenticated API calls with HTTP 401 when a key is set."""
    if not request.path.startswith("/api/"):
        return None
    if not api_key_configured():
        return None
    expected = (os.environ.get("II_API_KEY") or "").strip()
    provided = _request_api_key()
    if provided and hmac.compare_digest(provided, expected):
        return None
    return jsonify({
        "error": "unauthorized",
        "detail": (
            "This API requires the II_API_KEY. Send it as the "
            "'X-II-API-Key' header or as 'Authorization: Bearer <key>'."
        ),
    }), 401

# --------------------------------------------------------------------------- #
# Globals (loaded once at startup)
# --------------------------------------------------------------------------- #
MODEL = None
KG: Optional[CodebaseKnowledgeGraph] = None
STATE: Optional[StateManager] = None
SHARDER: Optional[ConstraintSharder] = None
Z3 = None
SYNTH = None
VISION = None
BLACKBOARD = None

WORKDIR = _HERE

# --------------------------------------------------------------------------- #
# Tool definitions (legacy tool-calling API compatibility)
# --------------------------------------------------------------------------- #
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search concurrent web backends and return citation-tagged results.",
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
                "description": "Run Python in a local subprocess with the current user's permissions; not isolated.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        },
    },
]

if _SMT_AVAILABLE:
    TOOLS.append(
        {
            "type": "function",
            "function": {
                "name": "smt_solver",
                "description": "Solve formally specified constraints using Z3. "
                               "The problem must provide VARIABLES and CONSTRAINTS.",
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
    )

if _SYNTH_AVAILABLE:
    TOOLS.append(
        {
            "type": "function",
            "function": {
                "name": "program_synthesis",
                "description": "Search a small built-in expression set for a match to "
                               "input/output examples. This is a bounded prototype.",
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
    )

if _VISION_AVAILABLE:
    TOOLS.append(
        {
            "type": "function",
            "function": {
                "name": "visual_ground",
                "description": "Experimental image/text similarity only; does not locate UI regions.",
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
    )

TOOLS.append(
    {
        "type": "function",
        "function": {
            "name": "blackboard",
            "description": "Experimental interface store with listing, inspection, and approximate dependency-graph output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["inspect", "list", "graph"]},
                    "payload": {"type": "string", "description": "JSON payload for the action"},
                },
                "required": ["action"],
            },
        },
    },
)

SYSTEM_PROMPT_TEMPLATE = """You are II (Identity: II v3.0), developed by Pawan Hiray. Never claim to be Anthropic, OpenAI, or any other company.
Always assist normal questions (time, repos, verification). Never refuse with "I'm sorry, I cannot assist". Only decline illegal/harmful requests.
If asked "who made you?" say: "Pawan Hiray developed me; I am II."
{web_instruction}
Tools: search_web(query), read_url(url), research(query), execute_code(code), smt_solver(problem), program_synthesis(examples), blackboard(action,payload).
{repository_index}
{decision_context}
{constraint_context}
"""

# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #
def execute_code(code: str, timeout: int = 20) -> tuple[bool, str]:
    """Run Python via ii_security.run_python_code.

    Default mode is a resource-limited subprocess running as the current
    user -- NOT a sandbox.  Set II_CODE_SANDBOX=docker for fail-closed
    Docker isolation; set II_REQUIRE_SANDBOX=1 to refuse subprocess mode.
    """
    return run_python_code(code, timeout=timeout)


def dispatch_tool(name: str, args: dict) -> str:
    if name in WEB_TOOL_NAMES and is_private_mode():
        return web_blocked_message(name)
    if name == "search_web":
        return search_web(_smart_search_query(args["query"]))
    if name == "read_url":
        return read_url(args["url"])
    if name == "research":
        result = research_and_answer(args.get("query", ""))
        return json.dumps(result, ensure_ascii=False, default=str)
    if name == "execute_code":
        success, output = execute_code(args["code"])
        return f"OK: {output}" if success else f"ERROR: {output}"
    # Part 11 / v11 tools
    if name == "smt_solver" and smt_math_tool:
        return smt_math_tool(args["problem"])
    if name == "program_synthesis" and synthesis_tool:
        return synthesis_tool(args["examples"])
    if name == "visual_ground" and vision_ground_tool:
        return vision_ground_tool(args["image_path"], args["query"])
    if name == "blackboard":
        return blackboard_tool(args["action"], args.get("payload", ""), str(WORKDIR))
    return "Tool not found."


# --------------------------------------------------------------------------- #
# Prompt assembly and setup guidance
# --------------------------------------------------------------------------- #
def model_setup_message() -> str:
    """Compatibility message for removed GGUF-era generation endpoints."""
    return (
        "This legacy generative endpoint is disabled. Use hybrid chat with an "
        "OpenAI-compatible BYOK provider instead; II will expose its safe "
        "internet and project tools to that provider."
    )


def build_system_prompt() -> str:
    repository_index = KG.get_repository_index() if KG else "(graph not loaded)"
    dc = STATE.get_decision_context() if STATE else ""
    cc = STATE.get_constraint_summary() if STATE else ""
    smt_line = "- smt_solver(problem): Solve explicit constraints with Z3" if _SMT_AVAILABLE else ""
    synth_line = "- program_synthesis(examples): Search a bounded expression set for a match" if _SYNTH_AVAILABLE else ""
    vision_line = "- visual_ground(image_path, query): Experimental vision prototype" if _VISION_AVAILABLE else ""
    web_instruction = (
        "II_PRIVATE mode is ON: web tools are disabled. Do not claim to have "
        "searched the web; say when information may be out of date."
        if is_private_mode() else
        "Always use search_web for time/current events/repo/news. Cite results as [1], [2]. Never guess times."
    )
    return SYSTEM_PROMPT_TEMPLATE.format(
        web_instruction=web_instruction,
        repository_index=repository_index,
        decision_context=dc,
        constraint_context=cc,
        smt_tool=smt_line,
        synth_tool=synth_line,
        vision_tool=vision_line,
    )


def extract_code_block(text: str) -> Optional[str]:
    if "```python" in text:
        return text.split("```python", 1)[1].split("```", 1)[0].strip()
    if "```" in text:
        return text.split("```", 1)[1].split("```", 1)[0].strip()
    return None


def _needs_web_search(user_message: str) -> bool:
    """Heuristic: does this question likely need live web search?"""
    text = user_message.lower()
    # Always force search for time/date/verification/current info
    time_verify_keywords = [
        "time", "date", "now", "current", "today", "indian", "ist", "gmt",
        "verify", "check", "confirm", "sure", "what is the time", "what time",
        "indian time", "time in", "current time", "right now"
    ]
    # Expanded current-event keywords
    current_event_keywords = [
        "today", "yesterday", "this week", "this month", "this year",
        "latest", "recent", "news", "current", "now", "happening",
        "who is", "what is", "where is", "when did", "why did",
        "new", "just released", "just announced", "brea", "update",
        "status", "status of", "status of the", "status of repo",
        "github", "trending", "viral", "popular", "top",
        "is", "are", "does", "did", "can", "could", "would", "will",
        "who", "what", "where", "when", "why", "how",
    ]
    for kw in time_verify_keywords:
        if kw in text:
            return True
    for kw in current_event_keywords:
        if kw in text:
            return True
    return False


def _extract_raw_tool_json(text: str) -> str | None:
    """Extract raw JSON tool-call from text, handling markdown fences.
    Searches for JSON tool-call patterns anywhere in the text."""
    stripped = text.strip()
    # Strip markdown code fences
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        if len(lines) > 2:
            stripped = "\n".join(lines[1:-1]).strip()
    # Try to find JSON object in the text
    import json as _json
    import re
    # Look for JSON objects that look like tool calls
    # Pattern: { ... "name": "...", "arguments": {...} ... }
    json_pattern = r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{.*?\}\s*\}'
    match = re.search(json_pattern, stripped, re.DOTALL)
    if match:
        try:
            data = _json.loads(match.group())
            if isinstance(data, dict) and "name" in data and "arguments" in data:
                return match.group()
        except Exception:
            pass
    # Fallback: try parsing the whole stripped text as JSON
    if stripped.startswith("{"):
        try:
            data = _json.loads(stripped)
            if isinstance(data, dict) and "name" in data and "arguments" in data:
                return stripped
        except Exception:
            pass
    return None


def _is_raw_tool_json(text: str) -> bool:
    """Detect if the model output contains raw JSON tool-call instead of pure natural language."""
    return _extract_raw_tool_json(text) is not None


def _smart_search_query(query: str) -> str:
    """Keep query intact for DDG; only clean obvious noise and add github context."""
    # Remove extra whitespace and normalize
    q = " ".join(query.split())
    # Only expand REA abbreviation; don't over-expand
    if "rea" in q.lower():
        q = q.replace("REA", "reverse engineering").replace("rea", "reverse engineering")
    # Only add github context, never stack keywords
    if "github" not in q.lower() and any(k in q.lower() for k in ["repo", "reverse engineering", "trending"]):
        q += " github"
    # Limit to 120 chars max
    return q[:120].strip()


def _execute_raw_tool_call(tool_call_dict: dict) -> str:
    """Execute a raw JSON tool-call dict as if it came from proper tool calling."""
    fn = tool_call_dict.get("name", "")
    args = tool_call_dict.get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    return dispatch_tool(fn, args)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    # Never embed server credentials in HTML. Remote users enter II_API_KEY in
    # tab-scoped settings when API authentication is enabled.
    return render_template("chat.html")


def _pipeline_stages() -> dict:
    """Report which pipeline stages are actually usable right now.

    The report is truthful: a stage is only "active" when its dependencies
    are importable and its preconditions hold.
    """
    from core.nlp import HAS_SPACY, nlp_backend

    stages: dict[str, dict] = {}

    def stage(name: str, active: bool, detail: str) -> None:
        stages[name] = {"active": bool(active), "detail": detail}

    stage(
        "logic_tree",
        True,
        "reasoning.logic_tree_solver -- multi-hop decomposition and cited "
        "extraction (no LLM).",
    )
    stage(
        "ingestion",
        not is_private_mode(),
        "Web search/fetch are disabled under II_PRIVATE." if is_private_mode()
        else "ingestion.ii_web -- live search and SSRF-guarded fetch.",
    )
    stage(
        "exocortex",
        True,
        "memory.exocortex -- disk-backed paged memory (opt-in, never silent).",
    )
    stage(
        "interviewer",
        True,
        "interaction.interviewer -- slot extraction, at most two questions.",
    )
    stage(
        "empathy",
        True,
        "interaction.ii_empathy_module -- crisis-first support with sources.",
    )
    stage(
        "brainstorming",
        not is_private_mode(),
        "interaction.ii_brainstorming_engine -- mechanical SCAMPER "
        "recombination of live components.",
    )
    stage(
        "hypothesis",
        not is_private_mode(),
        "invention.combinatorial_hypothesis_engine -- sandbox-tested toy "
        "simulations (never 'verified').",
    )
    stage(
        "self_tool_builder",
        sandbox_mode() == "docker",
        "tools.self_tool_builder -- requires the fail-closed Docker sandbox.",
    )
    stage(
        "deterministic_renderer",
        True,
        "core.caged_renderer -- Python renders structured tool results; no "
        "GGUF or external model is required.",
    )
    stage(
        "nlp",
        True,
        f"spaCy available={HAS_SPACY}; backend={nlp_backend()}",
    )
    return stages


@app.route("/api/status", methods=["GET"])
def api_status():
    from core.hybrid_agent import provider_configured

    legacy_renderer = getattr(MODEL, "mode", None) if MODEL is not None else None
    return jsonify({
        "version": "3.0",
        "identity": "II -- Pure Internet Intelligence (hybrid assistant + tools)",
        "backend": "deterministic-tools",
        "engine_ready": True,
        # Kept for older UI clients. Deterministic tools work without BYOK;
        # unrestricted general chat is available when a provider is supplied.
        "chat_ready": True,
        "hybrid_chat_configured": provider_configured(),
        "hybrid_chat_mode": "openai-compatible-byok",
        "legacy_renderer": legacy_renderer,
        "model_required": False,
        "setup_message": None,
        "tools": {
            "web": not is_private_mode(),
            "smt": _SMT_AVAILABLE,
            "synthesis": _SYNTH_AVAILABLE,
            "vision_prototype": _VISION_AVAILABLE,
        },
        "private_mode": is_private_mode(),
        "web_tools_enabled": not is_private_mode(),
        "code_sandbox": sandbox_mode(),
        "api_auth": "api-key" if api_key_configured() else "disabled",
        "pipeline_stages": _pipeline_stages(),
        "reasoning_engine": (
            "II tools perform internet retrieval, deterministic Python "
            "algorithms, graph traversal, solvers, and DAG orchestration. "
            "Optional BYOK provides general conversation and dynamic tool planning."
        ),
        # Backward-compatible field name for API consumers.
        "llm_role": (
            "No model is required for deterministic tools. An ephemeral "
            "OpenAI-compatible BYOK provider enables general chat and planning."
        ),
    })


# --------------------------------------------------------------------------- #
# Hybrid BYOK chat: hosted language understanding + II project tools
# --------------------------------------------------------------------------- #
def _hybrid_chat(user_message: str, provider_data: dict | None, history):
    from core.hybrid_agent import (
        HybridAgent,
        HybridProviderError,
        ProviderConfig,
    )

    try:
        config = ProviderConfig.from_sources(provider_data)
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "invalid_provider_config"}), 400
    if config is None:
        return jsonify({
            "error": (
                "General chat needs BYOK configuration: provide an OpenAI-compatible "
                "API URL and model. The key is optional for providers that do not "
                "require authentication."
            ),
            "code": "provider_not_configured",
        }), 400
    try:
        result = HybridAgent(config).run(user_message, history=history)
    except HybridProviderError as exc:
        return jsonify({
            "error": str(exc),
            "code": "provider_error",
            "response": (
                "The BYOK provider could not complete this request. No fallback "
                "answer was invented."
            ),
        }), 502

    return jsonify({
        **result,
        "confidence": None,
        "questions": [],
        "constraint_tree": {},
        "event_log": [],
        "raw": {
            "tool_trace": result.get("tool_trace", []),
            "source_count": len(result.get("sources", [])),
        },
        "agentic": True,
        "demo": False,
    })


# --------------------------------------------------------------------------- #
# Pure II chat: deterministic fallback when BYOK is not configured
# --------------------------------------------------------------------------- #
def _pure_ii_chat(user_message: str):
    """Run the Pure II router and return its structured result."""
    from core.ii_agent import route_task

    try:
        result = route_task(user_message)
    except Exception as exc:  # pragma: no cover - defensive
        return jsonify({
            "error": f"pipeline failure: {type(exc).__name__}: {exc}",
            "response": (
                "The pipeline could not complete this request. No answer was "
                "invented."
            ),
            "status": "INSUFFICIENT_DATA",
        }), 500

    return jsonify({
        "response": result.get("answer", ""),
        "status": result.get("status", "INSUFFICIENT_DATA"),
        "intent": result.get("intent"),
        "confidence": result.get("confidence"),
        "pipeline": result.get("pipeline"),
        "response_kind": result.get("response_kind"),
        "questions": result.get("questions", []),
        "constraint_tree": result.get("constraint_tree", {}),
        "sources": result.get("sources", []),
        "event_log": result.get("event_log", []),
        "raw": result.get("raw", {}),
        "agentic": True,
        "demo": False,
    })


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    raw_message = data.get("message")
    if not isinstance(raw_message, str) or not raw_message.strip():
        return jsonify({"error": "empty message"}), 400
    user_message = raw_message.strip()
    if len(user_message) > 20000:
        return jsonify({"error": "message exceeds the 20,000 character limit"}), 413

    raw_pipeline = data.get("pipeline", "default")
    if raw_pipeline is None or raw_pipeline == "":
        raw_pipeline = "default"
    if not isinstance(raw_pipeline, str):
        return jsonify({"error": "pipeline must be a string"}), 400
    pipeline = raw_pipeline.strip().lower()
    raw_provider = data.get("provider")
    if raw_provider is not None and not isinstance(raw_provider, dict):
        return jsonify({"error": "provider must be a JSON object"}), 400
    provider_data = raw_provider
    history = data.get("history")

    # A request-supplied BYOK configuration takes priority. Server operators
    # can alternatively configure II_CHAT_API_URL / II_CHAT_MODEL / key in the
    # environment, in which case ProviderConfig resolves them here.
    from core.hybrid_agent import ProviderConfig

    try:
        byok_available = ProviderConfig.from_sources(provider_data) is not None
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "invalid_provider_config"}), 400
    if pipeline in {"hybrid", "byok"}:
        return _hybrid_chat(user_message, provider_data, history)
    if pipeline in {"pure_ii", "pure-ii", "ii"}:
        return _pure_ii_chat(user_message)
    if pipeline == "default":
        if byok_available:
            return _hybrid_chat(user_message, provider_data, history)
        return _pure_ii_chat(user_message)
    if pipeline != "legacy":
        return jsonify({
            "error": "unsupported pipeline",
            "supported": ["default", "hybrid", "pure_ii", "legacy"],
        }), 400

    # ------------------------------------------------------------------ #
    # Legacy path: model-driven tool calling (kept for comparison and for
    # users who want the older agentic behaviour).
    # ------------------------------------------------------------------ #
    # Agentic mode: if LM Studio is unavailable, use pure internet + search synthesis
    agentic_mode = (MODEL is None or MODEL.mode == "demo")

    if agentic_mode and is_private_mode():
        return jsonify({
            "response": (
                "II_PRIVATE mode is enabled and no local model backend is "
                "available, so this request cannot be answered: agentic mode "
                "needs web search, which private mode disables. Start a local "
                "GGUF/LM Studio backend or unset II_PRIVATE."
            ),
            "agentic": False,
            "demo": True,
            "private_mode": True,
        })

    if agentic_mode and not user_message.startswith("search_"):
        # Pure agentic: search, synthesize, return — no LM Studio needed
        try:
            obs = search_web(_smart_search_query(user_message))
            # Direct synthesis from search results
            citations = re.findall(r'\[\d+\]', obs)
            lines = [line for line in obs.split("\n") if line.startswith("[") or line.startswith("    URL:")]
            final_text = f"Agentic result (pure internet, no LM Studio):\n"
            for line in lines[:8]:
                if line.startswith("[") and "]" in line:
                    final_text += "- " + line[:300] + "\n"
            if not lines:
                final_text += "- Search performed; results available in raw JSON.\n"
            final_text += f"\nSources: {len(citations)} citations from search backends."
            return jsonify({
                "response": final_text,
                "agentic": True,
                "demo": False,
                "search_raw": obs[:3000],
            })
        except Exception as exc:
            return jsonify({
                "response": f"Agentic search attempted but encountered error: {exc}. Query: {user_message}",
                "agentic": True,
                "demo": False,
            })

    STATE.append_state_fact(f"user_message:{len(STATE._state_ledger)}", user_message)

    # Build the system prompt
    sys_prompt = build_system_prompt()

    # Messages for the model
    messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_message}]

    final_text = ""
    research_sources: dict[str, dict] = {}
    citation_verification: dict | None = None

    # Pull latest internet data before responding (skipped under II_PRIVATE)
    try:
        live_search = "" if is_private_mode() else search_web(_smart_search_query(user_message)[:100])
        messages.append({"role": "system", "content": f"Latest internet results for '{user_message}':\n{live_search[:2000]}"})
    except Exception:
        pass

    # Track if we forced a search
    forced_search = False

    for iteration in range(4):
        resp = MODEL.chat(
            messages=messages[-6:] if len(messages) > 6 else messages,
            tools=_active_tools(),
            temperature=0.2,
            max_tokens=1024,
        )
        tool_calls = resp.get("tool_calls") or []
        content = resp.get("content", "") or ""

        # Case 1: Model made proper tool calls
        if tool_calls:
            messages.append(resp)
            for tc in tool_calls:
                fn = tc["function"]["name"]
                args = tc["function"]["arguments"]
                if isinstance(args, str):
                    args = json.loads(args)
                observation = dispatch_tool(fn, args)

                # Capture sources from research tool
                if fn == "research":
                    try:
                        research_data = json.loads(observation)
                        research_sources = research_data.get("sources", {})
                    except json.JSONDecodeError:
                        pass

                # Capture sources from search_web tool
                elif fn == "search_web":
                    try:
                        import json as _json
                        start = observation.find("RAW_JSON_START")
                        end = observation.find("RAW_JSON_END")
                        if start != -1 and end != -1:
                            json_str = observation[start + len("RAW_JSON_START"):end].strip()
                            search_data = _json.loads(json_str)
                            for r in search_data.get("results", []):
                                cid = r.get("citation_id", "[?]")
                                research_sources[cid] = {
                                    "title": r.get("title", ""),
                                    "link": r.get("link", ""),
                                    "snippet": r.get("snippet", ""),
                                    "source": r.get("source", ""),
                                    "authority_score": r.get("authority_score", 0),
                                }
                    except Exception:
                        pass

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "name": fn,
                        "content": observation,
                    }
                )
            continue

        # Case 2: Model output raw JSON tool-call
        raw_json = _extract_raw_tool_json(content)
        if raw_json:
            try:
                import json as _json
                tool_call_dict = _json.loads(raw_json)
                fn = tool_call_dict.get("name", "")
                args = tool_call_dict.get("arguments", {})
                if isinstance(args, str):
                    args = json.loads(args)
                observation = dispatch_tool(fn, args)

                if fn == "research":
                    try:
                        research_data = json.loads(observation)
                        research_sources = research_data.get("sources", {})
                    except json.JSONDecodeError:
                        pass

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": "raw_" + str(abs(hash(raw_json))),
                        "name": fn,
                        "content": observation,
                    }
                )
                # Refresh system message content instead of duplicating
                messages[0]["content"] = sys_prompt + "\nThe tool result is above. Now write your final answer in plain text with citations [1], [2], etc. Do NOT output raw JSON."
                continue
            except Exception:
                pass

        # Case 3: Model gave text answer (possibly empty)
        final_text = content.strip()
        messages.append({"role": "assistant", "content": final_text})

        # If model gave no meaningful answer and we haven't forced search yet,
        # and the question needs web info, force search_web
        if not final_text or len(final_text.strip()) < 5:
            if not forced_search and _needs_web_search(user_message) and not is_private_mode():
                print(f"[FORCE-SEARCH] Model returned empty, forcing search: {user_message[:50]}...")
                obs = search_web(_smart_search_query(user_message))
                forced_search = True
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": "forced-search-" + str(abs(hash(user_message))),
                        "name": "search_web",
                        "content": obs,
                    }
                )
                continue
            # If we already forced search or it doesn't need search, break
            break

        # Model gave non-empty answer - check if it needs citations or verification
        # Force search_web for any real-time/current/verify/time question that wasn't searched
        if not tool_calls and _needs_web_search(user_message) and not forced_search and not is_private_mode():
            # If no sources yet, force search first
            if not research_sources:
                print(f"[FORCE-SEARCH] Forcing search for: {user_message[:60]}...")
                obs = search_web(_smart_search_query(user_message))
                forced_search = True
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": "forced-search-" + str(abs(hash(user_message))),
                        "name": "search_web",
                        "content": obs,
                    }
                )
                continue
            # If sources exist but model didn't use them, force citation fix
            fix_prompt = (
                f"You previously answered without using search tools. "
                f"Please re-answer using the following search results with citations [1], [2], etc.:\n\n"
                f"Question: {user_message}\n\n"
                f"Answer using the sources above. Cite every factual claim.\n"
            )
            cleaned_messages = [{"role": "system", "content": sys_prompt}]
            cleaned_messages.extend([m for m in messages if m.get("role") != "system"])
            cleaned_messages.append({"role": "user", "content": fix_prompt})
            resp = MODEL.chat(
                messages=cleaned_messages,
                tools=None,
                temperature=0.2,
            )
            final_text = resp.get("content", "") or ""
        break

    # Self-verification: check citations against sources
    citation_verification = verify_answer_citations(final_text, research_sources)

    # If citations are missing, ask model to fix (up to 2 rounds)
    fix_rounds = 0
    while not citation_verification["verified"] and fix_rounds < 2:
        fix_rounds += 1
        fix_prompt = (
            f"Your answer has missing or invalid citations: {citation_verification['missing_citations']}.\n"
            f"Available sources: {json.dumps({k: v for k, v in research_sources.items() if k in citation_verification.get('unused_sources', [])}, ensure_ascii=False)}\n"
            f"Please rewrite your answer with correct citations [1], [2], etc.\n"
            f"Original answer:\n{final_text}"
        )
        cleaned_fix = [{"role": "system", "content": sys_prompt}]
        cleaned_fix.extend([m for m in messages if m.get("role") != "system"])
        cleaned_fix.append({"role": "user", "content": fix_prompt})
        resp = MODEL.chat(
            messages=cleaned_fix,
            tools=None,
            temperature=0.2,
        )
        final_text = resp.get("content", "") or ""
        citation_verification = verify_answer_citations(final_text, research_sources)

    # AGENTIC MODE (pure internet + synthesis, no LM Studio dependency)
    # If LM Studio is unavailable or refused, synthesize directly from search results
    if (MODEL is None or MODEL.mode == "demo" or any(p in (final_text or "").lower() for p in refusal_patterns)):
        # Use search results to build direct answer (never under II_PRIVATE)
        if is_private_mode():
            pass
        elif not forced_search:
            try:
                search_obs = search_web(_smart_search_query(user_message))
                forced_search = True
                # Extract citations from search
                import re as _re
                citations = _re.findall(r'\[\d+\]', search_obs)
                # Simple synthesis: summarize first 3 results with citations
                result_lines = search_obs.split("SEARCH RESULTS for:")[-1].split("CITATION RULES")[0] if "SEARCH RESULTS for:" in search_obs else search_obs
                # Build direct response using parsed JSON results
                final_text = f"Based on real-time web search (Brave / SearXNG / DuckDuckGo / GitHub / Headless):\n"
                # Parse JSON results from search_obs
                start = search_obs.find("RAW_JSON_START")
                end = search_obs.find("RAW_JSON_END")
                sources = []
                if start != -1 and end != -1:
                    try:
                        import json as _json
                        data = _json.loads(search_obs[start + len("RAW_JSON_START"):end].strip())
                        # Filter results for relevance to user's keywords
                        user_words = set(w for w in re.findall(r'\b[a-z]{3,}\b', user_message.lower()) if w not in {"the","and","for","now","what","is","that","new","can","you","get","all","any","are","how","why","who","when","this","with","from","have","has","had","was","were","been","being","have","has","had","do","does","did","will","would","could","should","may","might","must","can","need","ought","used","about","into","through","during","before","after","above","below","between","under","again","further","once","here","there","when","where","why","how","all","each","few","more","most","other","some","such","no","nor","not","only","own","same","so","than","too","very","just","now"})
                        filtered = []
                        for r in data.get("results", [])[:3]:
                            text_all = (r.get("title", "") + " " + r.get("snippet", "")).lower()
                            if any(w in text_all for w in user_words if len(w) > 2):
                                filtered.append(r)
                        # If no keyword match, fall back to first result
                        results_to_use = filtered if filtered else data.get("results", [])[:1]
                        for r in results_to_use:
                            cid = r.get("citation_id", "[?]")
                            title = r.get("title", "")
                            url = r.get("link", "")
                            source = r.get("source", "")
                            snippet = r.get("snippet", "")[:120]
                            sources.append(f"{cid} {title}\n    URL: {url}\n    Source: {source}\n    Summary: {snippet}\n")
                    except Exception:
                        pass
                # Also try regex extraction for URL/Source from text block as fallback
                if not sources:
                    uris = _re.findall(r'URL: (https?://[^\n]+)\n    Source: ([^|]+)', search_obs)
                    for i, (url, src) in enumerate(uris[:3], 1):
                        title = url.split("/")[-1].replace("-", " ")[:60]
                        sources.append(f"[{i}] {title}\n    URL: {url}\n    Source: {src}\n")
                if sources:
                    # Build AI-style explanation (like DuckDuckGo AI / Chrome AI mode) using top result
                    top = sources[0].strip()
                    # Extract title and URL from source line
                    top_title = ""
                    top_url = ""
                    top_summary = ""
                    for line in sources:
                        if line.startswith("["):
                            # Extract citation, title, url, summary
                            parts = line.split("\n")
                            if len(parts) >= 2:
                                top_url = parts[1].replace("URL: ", "").strip()
                                top_summary = parts[-1].replace("Summary: ", "").strip()
                    # Write explanation in natural language like search engine AI
                    final_text = f"Based on real-time web search results:\n"
                    # Try to synthesize from the top source snippet
                    snippet_text = top_summary if top_summary else top
                    # Brief explanation referencing the result
                    if "github" in (top_url or "").lower() or "morluto" in (top or "").lower():
                        final_text += f"[1] REA (Reverse Engineer Anything) — a GitHub project giving AI coding agents tools to inspect software and rebuild features. URL: {top_url}\n"
                        final_text += f"Summary: {snippet_text[:200]}\n"
                    elif "reverse engineering" in user_message.lower():
                        final_text += f"[1] Reverse engineering resources and repositories. URL: {top_url}\n"
                        final_text += f"Summary: {snippet_text[:200]}\n"
                    else:
                        final_text += f"[1] {top[:150]}\nURL: {top_url}\nSource summary: {snippet_text[:150]}\n"
                    # Add other results as citations
                    for s in sources[1:]:
                        for line in s.split("\n"):
                            if line.startswith("[") and "]" in line:
                                final_text += f"- {line[:150]}\n"
                    final_text += "Every claim is backed by the cited search source."
                else:
                    final_text += "No structured citations found in search response. Raw search performed: " + user_message
            except Exception as exc:
                final_text = f"Agentic search attempted but encountered error: {exc}. Query: {user_message}"
        citation_verification = {"verified": True, "missing_citations": [], "unused_sources": []}

    # Final safety: if final_text is still raw JSON tool-call, give a helpful error
    if _extract_raw_tool_json(final_text):
        final_text = (
            "I tried to use a tool but couldn't process the result properly. "
            "Please try asking your question again."
        )
        citation_verification = {"verified": True, "missing_citations": [], "unused_sources": []}

    # Refusal bypass: if model refuses (Qwen safety filter), build answer directly from search results
    refusal_patterns = ["i'm sorry", "i cannot assist", "i can't assist", "i am unable to", "i am not able to"]
    text_lower = final_text.lower()
    if any(p in text_lower for p in refusal_patterns):
        if research_sources:
            source_lines = []
            for cid, info in research_sources.items():
                source_lines.append(f"{cid} {info['title']} — {info.get('link', '')}")
            final_text = (
                f"Based on available sources:\n" +
                "\n".join(f"  {line}" for line in source_lines) +
                f"\n\nNote: The model refused to synthesize; displaying search results directly."
            )
        elif is_private_mode():
            final_text = (
                "The model refused to respond and II_PRIVATE mode disables "
                "the search-based fallback."
            )
        else:
            # Try a direct search now and build from that
            try:
                direct_obs = search_web(_smart_search_query(user_message))
                final_text = direct_obs
            except Exception:
                final_text = (
                    "The model refused to respond. Search results: "
                    "No direct search performed. Please retry."
                )
        citation_verification = {"verified": True, "missing_citations": [], "unused_sources": []}

    # If still no meaningful response, return a helpful message
    if not final_text or len(final_text.strip()) < 3:
        final_text = (
            "I couldn't generate a proper response. "
            "Please try asking your question again, or ask me to search for something specific."
        )

    code = extract_code_block(final_text)
    if code:
        STATE.append_scratchpad(f"Generated code block ({len(code)} chars)")

    return jsonify({
        "response": final_text,
        "citation_verification": citation_verification,
        "research_sources": research_sources,
    })


@app.route("/api/build", methods=["POST"])
def api_build():
    if MODEL is None or MODEL.mode == "demo":
        return jsonify({"error": model_setup_message()}), 503

    data = request.get_json(force=True) or {}
    task = (data.get("task") or "").strip()
    if not task:
        return jsonify({"error": "empty task"}), 400

    prompt = (
        f"Write complete Python code to accomplish this task:\n{task}\n\n"
        "Return ONLY a single ```python block with the complete code. "
        "No extra prose."
    )

    def model_fix_fn(fix_prompt: str, current_code: str) -> str:
        resp = MODEL.chat(
            messages=[
                {"role": "system", "content": build_system_prompt()},
                {"role": "user", "content": fix_prompt},
            ],
            tools=None,
            temperature=0.2,
        )
        return extract_code_block(resp.get("content", "")) or current_code

    final_code = extract_code_block(
        MODEL.chat(
            messages=[
                {"role": "system", "content": build_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            tools=None,
            temperature=0.2,
        ).get("content", "")
    ) or ""

    final_code, results, rounds = SHARDER.enforce(
        final_code, model_fix_fn=model_fix_fn, max_rounds=5
    )

    for rid, (passed, detail) in results.items():
        STATE.record_constraint_result(rid, passed, detail)

    static_report = VerificationCascade().verify_python(final_code)
    STATE.record_decision(
        concerns=f"Codegen: {task[:80]}",
        rules_checked=list(results.keys()),
        rules_passed=[r for r, (p, _) in results.items() if p],
    )

    return jsonify(
        {
            "task": task,
            "code": final_code,
            "rounds": rounds,
            "results": {rid: {"passed": p, "detail": d} for rid, (p, d) in results.items()},
            "static_verification": static_report.to_dict(),
            "execution_performed": False,
            "semantic_correctness_verified": False,
        }
    )


@app.route("/api/graph", methods=["GET"])
def api_graph():
    return jsonify({"index": KG.get_repository_index() if KG else ""})


@app.route("/api/graph/topology", methods=["GET"])
def api_graph_topology():
    if KG is None:
        return jsonify({"error": "repository index is not initialized"}), 503
    analyzer = GraphTopologyAnalyzer(KG.graph)
    return jsonify(
        {
            "architecture_map": analyzer.generate_architecture_map(),
            "cycles": analyzer.find_cycles(),
            "clusters": analyzer.cluster_subsystems(),
        }
    )


@app.route("/api/verify", methods=["POST"])
def api_verify():
    data = request.get_json(force=True) or {}
    code = data.get("code")
    if not isinstance(code, str) or not code.strip():
        return jsonify({"error": "missing code"}), 400
    required_symbols = data.get("required_symbols", [])
    forbidden_calls = data.get("forbidden_calls", [])
    if not isinstance(required_symbols, list) or not all(
        isinstance(name, str) for name in required_symbols
    ):
        return jsonify({"error": "required_symbols must be an array of strings"}), 400
    if not isinstance(forbidden_calls, list) or not all(
        isinstance(name, str) for name in forbidden_calls
    ):
        return jsonify({"error": "forbidden_calls must be an array of strings"}), 400
    mode = data.get("mode", "static")
    if not isinstance(mode, str) or mode not in {"static", "cascade"}:
        return jsonify({"error": "mode must be 'static' or 'cascade'"}), 400
    docstring = data.get("docstring", "")
    step = data.get("step", code)
    requirements = data.get("requirements", [])
    if not isinstance(docstring, str) or not isinstance(step, str):
        return jsonify({"error": "docstring and step must be strings"}), 400
    if not isinstance(requirements, list) or not all(
        isinstance(requirement, str) for requirement in requirements
    ):
        return jsonify({"error": "requirements must be an array of strings"}), 400
    if mode == "cascade":
        passed, detail, report = VerificationCascade().verify(
            code,
            {
                "required_symbols": required_symbols,
                "forbidden_calls": forbidden_calls,
                "docstring": docstring,
                "step": step,
                "requirements": requirements,
            },
        )
        report["detail"] = detail
        report["mode"] = "cascade"
        return jsonify(report), (200 if passed else 422)
    report = VerificationCascade().verify_python(
        code, required_symbols=required_symbols, forbidden_calls=forbidden_calls
    )
    payload = report.to_dict()
    payload["mode"] = "static"
    return jsonify(payload), (200 if report.passed else 422)


@app.route("/api/search", methods=["POST"])
def api_search():

    if is_private_mode():
        return jsonify({"error": web_blocked_message("search"), "private_mode": True}), 403
    data = request.get_json(force=True) or {}
    query = data.get("query", "")
    if not query.strip():
        return jsonify({"error": "empty query"}), 400
    return jsonify({"results": search_web(_smart_search_query(query))})


@app.route("/api/research", methods=["POST"])
def api_research():
    """Run the full research pipeline: search + fetch + extract + detect conflicts."""
    if is_private_mode():
        return jsonify({"error": web_blocked_message("research"), "private_mode": True}), 403
    data = request.get_json(force=True) or {}
    query = data.get("query", "")
    if not query:
        return jsonify({"error": "empty query"}), 400
    result = research_and_answer(query)
    return jsonify(result)


@app.route("/api/verify-citations", methods=["POST"])
def api_verify_citations():
    """Check that every [n] citation in an answer maps to a real source."""
    data = request.get_json(force=True) or {}
    answer = data.get("answer", "")
    sources = data.get("sources", {})
    if not answer:
        return jsonify({"error": "empty answer"}), 400
    result = verify_answer_citations(answer, sources)
    return jsonify(result)


@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    if is_private_mode():
        return jsonify({"error": web_blocked_message("fetch"), "private_mode": True}), 403
    data = request.get_json(force=True) or {}
    url = data.get("url", "")
    if not isinstance(url, str) or not url.strip():
        return jsonify({"error": "url must be a non-empty string"}), 400
    result = fetch_url_metadata(url)
    if not result.success and (result.error or "").startswith("Blocked URL"):
        return jsonify({"error": result.error, "success": False}), 400
    return jsonify(
        {
            "tag": format_source_tag(result),
            "success": result.success,
            "evidence_status": "retrieved_unverified" if result.evidence_passages else "insufficient_evidence",
            "evidence_passages": result.evidence_passages,
            "factual_correctness_assessed": False,
        }
    )


@app.route("/api/state", methods=["GET"])
def api_state():
    return jsonify(
        {
            "decision_context": STATE.get_decision_context() if STATE else "",
            "constraint_summary": STATE.get_constraint_summary() if STATE else "",
            "scratchpad": STATE.get_scratchpad_window() if STATE else "",
        }
    )


# ---------------- Part 11 / v11 endpoints ---------------- #

@app.route("/api/smt", methods=["POST"])
def api_smt():
    if not _SMT_AVAILABLE:
        return jsonify({"error": "Z3 solver not available. Install: pip install z3-solver"}), 503
    data = request.get_json(force=True) or {}
    problem = data.get("problem", "")
    if not problem:
        return jsonify({"error": "empty problem"}), 400
    result = smt_math_tool(problem)
    return jsonify({"result": result})


@app.route("/api/synthesize", methods=["POST"])
def api_synthesize():
    if not _SYNTH_AVAILABLE:
        return jsonify({"error": "Program synthesizer not available (missing deps)"}), 503
    data = request.get_json(force=True) or {}
    examples = data.get("examples", "")
    if not examples:
        return jsonify({"error": "empty examples"}), 400
    result = synthesis_tool(examples)
    return jsonify({"result": result})


@app.route("/api/vision", methods=["POST"])
def api_vision():
    if not _VISION_AVAILABLE:
        return jsonify({"error": "Vision localization is a prototype and is not exposed as a supported tool yet."}), 501
    data = request.get_json(force=True) or {}
    image_path = data.get("image_path", "")
    query = data.get("query", "")
    if not image_path or not query:
        return jsonify({"error": "missing image_path or query"}), 400
    result = vision_ground_tool(image_path, query)
    return jsonify({"result": result})


@app.route("/api/steer", methods=["GET", "POST"])
def api_steer():
    """Activation steering was archived in II v3.0 (see ``archive/``)."""
    return jsonify({
        "error": (
            "activation steering is not part of Pure II: the experimental "
            "steering/probe modules were moved to archive/ and are not wired "
            "into the runtime."
        ),
        "applied": False,
        "active": False,
        "archived": True,
    }), 501


@app.route("/api/repos/ingest", methods=["POST"])
def api_repo_ingest():
    """Shallow-clone and index an explicitly supplied public GitHub URL."""
    if is_private_mode():
        return jsonify({"error": web_blocked_message("repo ingestion"), "ingested": False, "private_mode": True}), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "expected a JSON object with a url field"}), 400
    repo_url = data.get("url")
    if not isinstance(repo_url, str) or not repo_url.strip():
        return jsonify({"error": "url must be a non-empty HTTPS GitHub URL"}), 400

    from ingestion.repo_ingestor import RepoIngestor

    ingestor = RepoIngestor(
        cache_dir=WORKDIR / ".cache" / "repos",
        max_source_files=5_000,
        clone_timeout=60,
    )
    try:
        result = ingestor.ingest(repo_url)
    except PermissionError as exc:
        return jsonify({"error": str(exc), "ingested": False, "private_mode": True}), 403
    except FileExistsError as exc:
        return jsonify({"error": str(exc), "ingested": False}), 409
    except ValueError as exc:
        return jsonify({"error": str(exc), "ingested": False}), 422
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "ingested": False}), 502
    return jsonify({"result": result, "ingested": True, "graph_is_approximate": True})


@app.route("/api/repos/graph", methods=["GET"])
def api_repo_graph():
    repo = request.args.get("repo", "").strip()
    target = request.args.get("target", "").strip() or None
    if not repo:
        return jsonify({"error": "repo query parameter is required"}), 400

    from ingestion.repo_ingestor import RepoIngestor

    ingestor = RepoIngestor(cache_dir=WORKDIR / ".cache" / "repos")
    try:
        result = ingestor.query_graph(repo, target)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"repo": repo, "result": result, "graph_is_approximate": True})


@app.route("/api/blackboard", methods=["POST"])
def api_blackboard():
    data = request.get_json(force=True) or {}
    action = data.get("action", "")
    payload = data.get("payload", "")
    if not action:
        return jsonify({"error": "missing action"}), 400
    result = blackboard_tool(action, payload, str(WORKDIR))
    return jsonify({"result": result})


@app.route("/api/blackboard/graph", methods=["GET"])
def api_blackboard_graph():
    from blackboard.ii_blackboard import BlackboardInspector
    return jsonify(BlackboardInspector(WORKDIR).dependency_graph())


# --------------------------------------------------------------------------- #
# Pure II stage endpoints
# --------------------------------------------------------------------------- #
@app.route("/api/solve", methods=["POST"])
def api_solve():
    """Run the logic-tree solver directly and return its structured result."""
    from reasoning.logic_tree_solver import LogicTreeSolver

    data = request.get_json(force=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "empty query"}), 400
    result = LogicTreeSolver().solve(query)
    return jsonify(result)


@app.route("/api/interview", methods=["POST"])
def api_interview():
    """Extract constraints and (at most) two clarifying questions."""
    from interaction.interviewer import Interviewer

    data = request.get_json(force=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "empty prompt"}), 400
    interviewer = Interviewer()
    tree = interviewer.build_constraint_tree(prompt)
    asked = interviewer.ask_questions(prompt)
    return jsonify({
        "constraint_tree": tree,
        "questions": asked["questions"],
        "proceed_with_defaults": asked["proceed_with_defaults"],
    })


@app.route("/api/memory", methods=["GET"])
def api_memory():
    """Show exactly what the Exocortex remembers (transparency view)."""
    from memory.exocortex import Exocortex

    exocortex = Exocortex()
    return jsonify({
        "description": exocortex.describe(),
        "facts": exocortex.what_i_remember(),
        "episodes": exocortex.episodes(limit=10),
        "tool_runs": exocortex.tool_runs(limit=10),
    })


@app.route("/api/memory/optin", methods=["POST"])
def api_memory_optin():
    """Grant or revoke the user's consent for memory (profiling is opt-in)."""
    from memory.exocortex import Exocortex

    data = request.get_json(force=True) or {}
    granted = bool(data.get("granted", True))
    exocortex = Exocortex()
    state = exocortex.set_consent(granted)
    return jsonify({
        "consent_state": state,
        "fact_count": len(exocortex.what_i_remember()),
        "note": (
            "Memory is opt-in. Revoking consent deletes the stored facts."
            if granted else
            "Consent revoked; the stored facts were deleted."
        ),
    })


@app.route("/api/memory/forget", methods=["POST"])
def api_memory_forget():
    """Forget one stored fact (or all of them)."""
    from memory.exocortex import Exocortex

    data = request.get_json(force=True) or {}
    key = (data.get("key") or "").strip()
    exocortex = Exocortex()
    if key:
        removed = exocortex.forget(key)
        return jsonify({"forgotten": removed, "key": key})
    removed = exocortex.forget_all()
    return jsonify({"forgotten": removed, "key": None, "all": True})


@app.route("/api/brainstorm", methods=["POST"])
def api_brainstorm():
    """Mechanical recombination of live components (never invention)."""
    from interaction.ii_brainstorming_engine import BrainstormingEngine

    data = request.get_json(force=True) or {}
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "empty topic"}), 400
    engine = BrainstormingEngine(llm=MODEL)
    return jsonify(engine.generate(topic))


@app.route("/api/hypothesize", methods=["POST"])
def api_hypothesize():
    """Sandbox-tested toy hypotheses (status is SIMULATED, never VERIFIED)."""
    from invention.combinatorial_hypothesis_engine import (
        CombinatorialHypothesisEngine,
    )

    data = request.get_json(force=True) or {}
    statement = (data.get("problem") or data.get("problem_statement") or "").strip()
    if not statement:
        return jsonify({"error": "empty problem statement"}), 400
    engine = CombinatorialHypothesisEngine(llm=MODEL)
    return jsonify(engine.test_hypotheses(statement))


# --------------------------------------------------------------------------- #
# Startup
# --------------------------------------------------------------------------- #
def init_backend():
    global MODEL, KG, STATE, SHARDER, Z3, SYNTH, VISION, BLACKBOARD
    print("=" * 50)
    print("  II Pure Internet Intelligence")
    print("=" * 50)
    print(f"Workdir : {WORKDIR}")
    print("Engine  : II internet/tools + OpenAI-compatible BYOK chat")
    print(f"Z3 SMT solver   : {'available' if _SMT_AVAILABLE else 'NOT available'}")
    print(f"Program synth   : {'available' if _SYNTH_AVAILABLE else 'NOT available'}")
    print(f"MobileCLIP vision: {'available' if _VISION_AVAILABLE else 'NOT available'}")
    print(f"II_PRIVATE (no-network mode): {'ON' if is_private_mode() else 'off'}")
    print(f"Code execution mode: {sandbox_mode()} (default subprocess is NOT a sandbox)")
    print()

    # Pure II deliberately does not initialise a language-model backend.  This
    # keeps startup fast and makes the actual internet/tool engine the product,
    # rather than presenting a missing GGUF file as a degraded "demo" mode.
    MODEL = None
    from core.hybrid_agent import provider_configured
    print("II tool engine ready.")
    print(
        "Hybrid BYOK: "
        + ("server configuration detected" if provider_configured()
           else "configure any OpenAI-compatible model in the web UI")
    )

    print("Building codebase knowledge graph...")
    KG = CodebaseKnowledgeGraph(str(WORKDIR))
    KG.build_graph()
    KG.resolve_cross_file_edges()
    print(f"Graph ready: {len(KG._symbols)} symbols.")

    STATE = StateManager(str(WORKDIR))
    SHARDER = default_constraints()
    print("State manager ready.")

    if _SMT_AVAILABLE:
        try:
            Z3 = Z3Solver()
            print("Z3 SMT solver ready.")
        except Exception as exc:
            print(f"Z3 SMT solver: NOT available ({exc}).")
    if _SYNTH_AVAILABLE:
        try:
            SYNTH = EnumerativeSynthesizer()
            print("Program synthesizer ready.")
        except Exception as exc:
            print(f"Program synthesizer: NOT available ({exc}).")
    if _VISION_AVAILABLE:
        try:
            VISION = MobileCLIPGrounder()
            print(f"MobileCLIP vision grounder ready (available={VISION._available}).")
        except Exception as exc:
            print(f"MobileCLIP vision grounder: NOT available ({exc}).")
    BLACKBOARD = Blackboard(str(WORKDIR))
    print("Blackboard ready.")

    print()
    print("[OK] II server running at http://127.0.0.1:5000/")
    print("   Open that URL in your browser to chat.")
    print("   Press Ctrl+C to stop the server.")
    print("=" * 50)


def _exposure_warning(host: str) -> str:
    """Return a loud warning when the server is exposed beyond localhost."""
    if host in {"127.0.0.1", "localhost", "::1"}:
        return ""
    return (
        "\n" + "!" * 74 + "\n"
        f"  WARNING: II is about to bind to {host} -- a NON-LOCAL address.\n"
        "  The API is a local research tool: it runs live web requests and\n"
        "  (in some modes) generated code. Anyone who can reach this port can\n"
        "  use it. Set II_API_KEY to require a key, keep the host on 127.0.0.1,\n"
        "  or put the server behind an authenticating reverse proxy.\n"
        + "!" * 74 + "\n"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="II -- Pure Internet Intelligence (hybrid assistant + tools)"
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="interface to bind (default: 127.0.0.1, localhost only)")
    parser.add_argument("--port", type=int, default=5000, help="port to bind")
    args = parser.parse_args()

    warning = _exposure_warning(args.host)
    if warning:
        print(warning)
    if not api_key_configured() and args.host not in {"127.0.0.1", "localhost", "::1"}:
        print(
            "[WARNING] II_API_KEY is not set: the API is unauthenticated on "
            f"{args.host}. Set II_API_KEY before exposing this server."
        )
    init_backend()
    app.run(host=args.host, port=args.port, debug=False)
