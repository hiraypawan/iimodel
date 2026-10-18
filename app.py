"""
app.py — II local tool-augmented agent prototype.

The Flask UI can use a user-provided GGUF through llama-cpp-python or a local
LM Studio server. Without either backend, it starts in demo mode. Web search
and URL fetching make external network requests when those tools are used.

The API exposes experimental helpers for repository indexing, structured
constraint solving, bounded expression synthesis, and state inspection. These
helpers do not turn the application into a new foundation model.
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

# --------------------------------------------------------------------------- #
# Check for optional llama-cpp-python backend
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
    fetch_url_metadata,
    format_source_tag,
)
from ii_state import StateManager  # noqa: E402
from ii_constraints import ConstraintSharder, default_constraints  # noqa: E402
from graph.topology import GraphTopologyAnalyzer  # noqa: E402
from reasoning.verification_cascade import VerificationCascade  # noqa: E402

# Local GGUF model backend (supports llama-cpp-python, LM Studio, or demo fallback)
from llama_local import LocalModel  # noqa: E402

# Optional helper modules
try:
    from ii_smt import Z3Solver, smt_math_tool  # noqa: E402
    import z3 as _z3  # noqa: F401, E402
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
    # The current implementation returns image-level matches, not localized regions.
    # Keep it out of the agent's tool list until actual grounding is implemented.
    _VISION_AVAILABLE = False
except ImportError:
    MobileCLIPGrounder = None  # type: ignore[assignment]
    vision_ground_tool = None  # type: ignore[assignment]
    _VISION_AVAILABLE = False

from ii_blackboard import Blackboard, blackboard_tool  # noqa: E402  # pure Python

# --------------------------------------------------------------------------- #
# Flask app
# --------------------------------------------------------------------------- #
app = Flask(__name__, template_folder="templates", static_folder="static")

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
MODEL_FILENAME = "qwen2.5-1.5b-instruct-q4_0.gguf"
MODEL_PATH = WORKDIR / "models" / MODEL_FILENAME
MODEL_REPOSITORY_URL = "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF"
MODEL_FILE_URL = (
    "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/"
    "qwen2.5-1.5b-instruct-q4_0.gguf?download=true"
)
LMSTUDIO_URL = "http://127.0.0.1:1234"

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

SYSTEM_PROMPT_TEMPLATE = """You are II, a coding assistant connected to an optional local language model.
Tools are helpers, not guarantees. Web search and URL fetching make external requests.
The repository index below is approximate and may omit symbols:

{repository_index}

You also have these tools (call them by name when helpful):
- search_web(query): Search the web; this sends a request to an external service
- read_url(url): Return source URLs and extracted passages for review; this is not fact-checking
- execute_code(code): Run Python as a local subprocess; it is not isolated
{smt_tool}
{synth_tool}
{vision_tool}
- blackboard(action, payload): Experimental interface store and inspector

Guidance:
1. State uncertainty and verify important claims.
2. Treat retrieved passages as unverified excerpts; lexical conflict flags are review prompts only. Say “insufficient evidence” when appropriate.
3. Use constraint solving only when variables and constraints are explicit.
4. The synthesis helper searches a limited expression set; it is not general-purpose.
5. Do not describe local subprocess execution as a sandbox.
6. Keep answers concise and useful.

{decision_context}
{constraint_context}
"""

# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #
def execute_code(code: str, timeout: int = 20) -> tuple[bool, str]:
    """Run Python in a temporary subprocess (not an OS-level sandbox)."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(code)
        temp_path = f.name
    try:
        result = subprocess.run(
            [sys.executable, temp_path],
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
# Prompt assembly and setup guidance
# --------------------------------------------------------------------------- #
def model_setup_message() -> str:
    return (
        "No language-model backend is connected; II is in demo mode and cannot "
        "answer prompts with an AI model.\n\n"
        "Recommended: load Qwen2.5-1.5B-Instruct in LM Studio and start its "
        f"OpenAI-compatible server at {LMSTUDIO_URL}.\n"
        f"Alternative: download {MODEL_FILENAME} from {MODEL_FILE_URL} into "
        f"{MODEL_PATH}, then install llama-cpp-python.\n"
        f"Model repository: {MODEL_REPOSITORY_URL}"
    )


def build_system_prompt() -> str:
    repository_index = KG.get_repository_index() if KG else "(graph not loaded)"
    dc = STATE.get_decision_context() if STATE else ""
    cc = STATE.get_constraint_summary() if STATE else ""
    smt_line = "- smt_solver(problem): Solve explicit constraints with Z3" if _SMT_AVAILABLE else ""
    synth_line = "- program_synthesis(examples): Search a bounded expression set for a match" if _SYNTH_AVAILABLE else ""
    vision_line = "- visual_ground(image_path, query): Experimental vision prototype" if _VISION_AVAILABLE else ""
    return SYSTEM_PROMPT_TEMPLATE.format(
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


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return render_template("chat.html")


@app.route("/api/status", methods=["GET"])
def api_status():
    backend = MODEL.mode if MODEL is not None else "not_initialized"
    return jsonify({
        "backend": backend,
        "chat_ready": backend in ("gguf", "lmstudio"),
        "model_file_present": MODEL_PATH.is_file(),
        "setup_message": None if backend in ("gguf", "lmstudio") else model_setup_message(),
        "tools": {
            "smt": _SMT_AVAILABLE,
            "synthesis": _SYNTH_AVAILABLE,
            "vision_prototype": _VISION_AVAILABLE,
        },
    })


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True) or {}
    user_message = (data.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "empty message"}), 400

    if MODEL is None or MODEL.mode == "demo":
        return jsonify({
            "response": model_setup_message(),
            "demo": True,
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
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "name": fn,
                        "content": observation,
                    }
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

    data = request.get_json(force=True) or {}
    query = data.get("query", "")
    return jsonify({"results": search_web(query)})


@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    data = request.get_json(force=True) or {}
    url = data.get("url", "")
    result = fetch_url_metadata(url)
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
    from vibe.steering import SteeringController

    controller = SteeringController()
    if request.method == "GET":
        return jsonify({
            "supported_styles": controller.list_supported_styles(),
            "available_vectors": controller.list_available_vectors(),
            "applied": False,
            "active": False,
        })
    data = request.get_json(force=True) or {}
    action = data.get("action", "list")
    if action == "list":
        return jsonify({
            "supported_styles": controller.list_supported_styles(),
            "available_vectors": controller.list_available_vectors(),
            "applied": False,
            "active": False,
        })
    if action == "apply":
        return jsonify({
            "error": "activation steering is unavailable: the active model backend has no compatible hidden-state hook or reviewed vector",
            "applied": False,
        }), 501
    return jsonify({"error": "action must be 'list' or 'apply'"}), 400


@app.route("/api/repos/ingest", methods=["POST"])
def api_repo_ingest():
    """Shallow-clone and index an explicitly supplied public GitHub URL."""
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
    from ii_blackboard import BlackboardInspector
    return jsonify(BlackboardInspector(WORKDIR).dependency_graph())


# --------------------------------------------------------------------------- #
# Startup
# --------------------------------------------------------------------------- #
def init_backend():
    global MODEL, KG, STATE, SHARDER, Z3, SYNTH, VISION, BLACKBOARD
    print("=" * 50)
    print("  II local tool-augmented agent prototype")
    print("=" * 50)
    print(f"Workdir : {WORKDIR}")
    print(f"Model   : {MODEL_PATH}")
    print(f"Z3 SMT solver   : {'available' if _SMT_AVAILABLE else 'NOT available'}")
    print(f"Program synth   : {'available' if _SYNTH_AVAILABLE else 'NOT available'}")
    print(f"MobileCLIP vision: {'available' if _VISION_AVAILABLE else 'NOT available'}")
    print()

    if not MODEL_PATH.is_file():
        print(f"Model weights are not bundled; expected file: {MODEL_PATH}")
        print(f"Download page: {MODEL_REPOSITORY_URL}")
        print(f"Or use LM Studio at {LMSTUDIO_URL} with a compatible model loaded.")
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
        print("Demo mode - chat will show setup guidance, not model-generated answers.")
        print(model_setup_message())

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
    print("[OK] II prototype server running at http://127.0.0.1:5000/")
    print("   Open that URL in your browser to chat.")
    print("   Press Ctrl+C to stop the server.")
    print("=" * 50)


if __name__ == "__main__":
    init_backend()
    app.run(host="127.0.0.1", port=5000, debug=False)
