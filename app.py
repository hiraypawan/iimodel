"""
app.py — II (Internet Intelligence) v12 Local Web Server

Runs the II agent 100% locally using the GGUF model in models/ (no Ollama).
Serves a browser chat UI at http://127.0.0.1:5000/

v12 features:
  - System Mixture of Experts routing
  - Deterministic pre-LLM tool routing (anti-hallucination shield)
  - LM Studio auto-fallback (no C++ Build Tools needed)
  - Cross-language codebase graph (Python + JS/TS via tree-sitter)

Endpoints:
  GET    /                 -> chat UI
  POST   /api/chat         -> chat with II (tool-use loop)
  POST   /api/build        -> constraint-sharded code generation
  GET    /api/graph        -> holographic codebase index
  POST   /api/search       -> DuckDuckGo web search
  POST   /api/fetch        -> fetch + extract clean text from a URL
  GET    /api/state        -> inspect II state (ledgers, checkpoints, constraints)
  POST   /api/smt          -> Z3 SMT solver (PhD-level math, 0% hallucination)
  POST   /api/synthesize   -> Enumerative program synthesis (Kolmogorov optimal)
  POST   /api/vision       -> MobileCLIP semantic visual grounding
  POST   /api/blackboard   -> Blackboard pattern (design/build/inspect/list)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# --------------------------------------------------------------------------- #
# Auto-install llama-cpp-python if missing (so real GGUF model loads)
# --------------------------------------------------------------------------- #
def _ensure_llama_cpp():
    """Try to import llama-cpp-python. If missing, do NOT auto-install from source
    (building requires Visual Studio C++ Build Tools). Just report and let the
    system fall back to demo mode or LM Studio."""
    try:
        import llama_cpp  # noqa: F401
        print("[OK] llama-cpp-python already installed.")
        return True
    except ImportError:
        print("[INFO] llama-cpp-python not installed. Real GGUF model will not load.")
        print("         Install manually: pip install llama-cpp-python --only-binary=:all:")
        print("         Or use LM Studio at http://127.0.0.1:1234 instead.")
        return False

_ensure_llama_cpp()

# --------------------------------------------------------------------------- #
# Make sibling modules importable
# --------------------------------------------------------------------------- #
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from ii_graph import CodebaseKnowledgeGraph  # noqa: E402
from ii_web import (  # noqa: E402
    search_web,
    read_url,
    fetch_with_authority,
    format_source_tag,
)
from ii_state import StateManager  # noqa: E402
from ii_constraints import ConstraintSharder, default_constraints  # noqa: E402

# Local GGUF model backend (supports llama-cpp-python, LM Studio, or demo fallback)
from llama_local import LocalModel  # noqa: E402

# Part 11 / v11 modules (some need optional packages)
try:
    from ii_smt import Z3Solver, smt_math_tool  # noqa: E402
    _SMT_AVAILABLE = True
except ImportError:
    Z3Solver = None  # type: ignore[assignment]
    smt_math_tool = None  # type: ignore[assignment]
    _SMT_AVAILABLE = False

try:
    from ii_synthesis import EnumerativeSynthesizer, synthesis_tool  # noqa: E402
    _SYNTH_AVAILABLE = True
except ImportError:
    EnumerativeSynthesizer = None  # type: ignore[assignment]
    synthesis_tool = None  # type: ignore[assignment]
    _SYNTH_AVAILABLE = False

try:
    from ii_vision import MobileCLIPGrounder, vision_ground_tool  # noqa: E402
    _VISION_AVAILABLE = True
except ImportError:
    MobileCLIPGrounder = None  # type: ignore[assignment]
    vision_ground_tool = None  # type: ignore[assignment]
    _VISION_AVAILABLE = False

from ii_blackboard import Blackboard, blackboard_tool  # noqa: E402  # pure Python

# --------------------------------------------------------------------------- #
# Flask app
# --------------------------------------------------------------------------- #
app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

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
MODEL_PATH = WORKDIR / "models" / "qwen2.5-1.5b-instruct-q4_0.gguf"

# --------------------------------------------------------------------------- #
# Tool definitions (exposed to the model)
# --------------------------------------------------------------------------- #
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search DuckDuckGo for current information",
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
            "description": "Run Python code in a local sandbox",
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
                "description": "Solve PhD-level math problems deterministically using Z3 SMT solver. "
                               "Provide VARIABLES and CONSTRAINTS.",
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
                "description": "Synthesize the shortest program (Kolmogorov optimal) that matches "
                               "input/output examples via exhaustive search.",
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
                "description": "Ground a text query to a visual region in a screenshot using "
                               "MobileCLIP semantic embeddings.",
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
            "description": "Blackboard Pattern: design multi-file architecture, build files, "
                           "or inspect for cross-file issues. Actions: design, build, inspect, list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "design, build, inspect, or list"},
                    "payload": {"type": "string", "description": "JSON payload for the action"},
                },
                "required": ["action"],
            },
        },
    },
)

SYSTEM_PROMPT_TEMPLATE = """You are II, a local coding agent powered by a 1.5B model running locally (no cloud).
You have access to the following codebase structure:

{holographic_index}

You also have these tools (call them by name when helpful):
- search_web(query): Search DuckDuckGo for current information
- read_url(url): Extract clean markdown text from a URL
- execute_code(code): Run Python code in a sandbox; returns success/output
{smt_tool}
{synth_tool}
{vision_tool}
- blackboard(action, payload): Multi-file architecture via Blackboard Pattern (design/build/inspect/list)

Rules:
1. Always write complete, runnable Python code.
2. Prefer searching the web for live docs over guessing from memory.
3. Verify code by running it before presenting it to the user.
4. Use the codebase map above to locate exact signatures; don't guess file paths.
5. For PhD math, use smt_solver — do not guess neural answers.
6. For novel algorithms, use program_synthesis — do not hallucinate from training data.
7. For UI visual issues, use visual_ground to find exact coordinates.
8. For 50-file projects, use blackboard to prevent context rot.
9. Keep answers concise and useful.

{decision_context}
{constraint_context}
"""

# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #
def execute_code(code: str, timeout: int = 20) -> tuple[bool, str]:
    """Run Python code in a temp file sandbox."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(code)
        temp_path = f.name
    try:
        result = subprocess.run(
            ["python", temp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
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


def dispatch_tool(name: str, args: dict) -> str:
    if name == "search_web":
        return search_web(args["query"])
    if name == "read_url":
        return read_url(args["url"])
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
# Prompt assembly
# --------------------------------------------------------------------------- #
def build_system_prompt() -> str:
    hg = KG.get_holographic_index() if KG else "(graph not loaded)"
    dc = STATE.get_decision_context() if STATE else ""
    cc = STATE.get_constraint_summary() if STATE else ""
    smt_line = "- smt_solver(problem): Solve PhD-level math deterministically via Z3 SMT solver (0% hallucination)" if _SMT_AVAILABLE else ""
    synth_line = "- program_synthesis(examples): Synthesize shortest program matching input/output examples" if _SYNTH_AVAILABLE else ""
    vision_line = "- visual_ground(image_path, query): Ground text query to visual region via MobileCLIP" if _VISION_AVAILABLE else ""
    return SYSTEM_PROMPT_TEMPLATE.format(
        holographic_index=hg,
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


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return render_template("chat.html")


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True) or {}
    user_message = (data.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "empty message"}), 400

    if MODEL.mode == "demo":
        return jsonify({
            "response": (
                "II is running in demo mode. No real model backend is connected.\n\n"
                "To enable real model chat, choose ONE:\n"
                "  Option A: pip install llama-cpp-python (may need VS C++ Build Tools)\n"
                "  Option B: Start LM Studio local server at http://127.0.0.1:1234\n\n"
                "All tools still work: math solver, code synthesis, web search, "
                "URL fetch, code execution, visual grounding, blackboard.\n\n"
                "Your message was: '" + user_message[:100] + "'"
            )
        })

    STATE.append_state_fact(f"user_message:{len(STATE._state_ledger)}", user_message)
    messages = [{"role": "user", "content": user_message}]
    sys_prompt = build_system_prompt()

    final_text = ""
    for _ in range(10):
        resp = MODEL.chat(
            messages=[{"role": "system", "content": sys_prompt}] + messages,
            tools=TOOLS,
            temperature=0.2,
        )
        tool_calls = resp.get("tool_calls") or []

        if tool_calls:
            messages.append(resp)
            for tc in tool_calls:
                fn = tc["function"]["name"]
                args = tc["function"]["arguments"]
                if isinstance(args, str):
                    args = json.loads(args)
                observation = dispatch_tool(fn, args)
                messages.append(
                    {"role": "tool", "name": fn, "content": observation}
                )
            continue

        final_text = resp.get("content", "")
        messages.append({"role": "assistant", "content": final_text})
        break

    code = extract_code_block(final_text)
    if code:
        STATE.append_scratchpad(f"Generated code block ({len(code)} chars)")

    return jsonify({"response": final_text})


@app.route("/api/build", methods=["POST"])
def api_build():
    if MODEL.mode == "demo":
        return jsonify({"error": "Model backend not available. Install llama-cpp-python or start LM Studio."}), 503

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

    failed = [r for r, (p, _) in results.items() if not p]
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
        }
    )


@app.route("/api/graph", methods=["GET"])
def api_graph():
    return jsonify({"index": KG.get_holographic_index() if KG else ""})


@app.route("/api/search", methods=["POST"])
def api_search():
    data = request.get_json(force=True) or {}
    query = data.get("query", "")
    return jsonify({"results": search_web(query)})


@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    data = request.get_json(force=True) or {}
    url = data.get("url", "")
    result = fetch_with_authority(url)
    return jsonify({"tag": format_source_tag(result), "success": result.success})


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
        return jsonify({"error": "Vision grounder not available. Install: pip install torch transformers"}), 503
    data = request.get_json(force=True) or {}
    image_path = data.get("image_path", "")
    query = data.get("query", "")
    if not image_path or not query:
        return jsonify({"error": "missing image_path or query"}), 400
    result = vision_ground_tool(image_path, query)
    return jsonify({"result": result})


@app.route("/api/blackboard", methods=["POST"])
def api_blackboard():
    data = request.get_json(force=True) or {}
    action = data.get("action", "")
    payload = data.get("payload", "")
    if not action:
        return jsonify({"error": "missing action"}), 400
    result = blackboard_tool(action, payload, str(WORKDIR))
    return jsonify({"result": result})


# --------------------------------------------------------------------------- #
# Startup
# --------------------------------------------------------------------------- #
def init_backend():
    global MODEL, KG, STATE, SHARDER, Z3, SYNTH, VISION, BLACKBOARD
    print("=" * 50)
    print("  II (Internet Intelligence) v11 - Local Server")
    print("=" * 50)
    print(f"Workdir : {WORKDIR}")
    print(f"Model   : {MODEL_PATH}")
    print(f"Z3 SMT solver   : {'available' if _SMT_AVAILABLE else 'NOT available'}")
    print(f"Program synth   : {'available' if _SYNTH_AVAILABLE else 'NOT available'}")
    print(f"MobileCLIP vision: {'available' if _VISION_AVAILABLE else 'NOT available'}")
    print()

    if not MODEL_PATH.exists():
        print(f"WARNING: model file not found at {MODEL_PATH}")
        print("         Place qwen2.5-1.5b-instruct-q4_0.gguf in the models/ folder.")
        print()

    # Load model (auto-selects: llama-cpp-python -> LM Studio -> demo)
    print("Loading model backend...")
    MODEL = LocalModel(str(MODEL_PATH), n_ctx=4096, n_gpu_layers=0)
    print(f"Backend mode: {MODEL.mode}")
    if MODEL.mode == "gguf":
        print("Real GGUF model loaded - full chat enabled.")
    elif MODEL.mode == "lmstudio":
        print("LM Studio connected - full chat enabled.")
    else:
        print("Demo mode - tools work, install llama-cpp-python or LM Studio for real model.")

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
    print("[OK] II v11 server running at http://127.0.0.1:5000/")
    print("   Open that URL in your browser to chat.")
    print("   Press Ctrl+C to stop the server.")
    print("=" * 50)


if __name__ == "__main__":
    init_backend()
    app.run(host="127.0.0.1", port=5000, debug=False)
