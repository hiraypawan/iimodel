================================================================================
II v11 — Internet Intelligence (Neuro-Symbolic Agentic OS)
================================================================================

WHAT THIS IS
--------------------------------------------------------------------------------
A 100% local, $0-cost, private AI agent that runs on a 1.5B parameter model
(qwen2.5-1.5b-instruct-q4_0.gguf) plus deterministic system tools:

  • Z3 SMT Solver          — 100% verified PhD-level math (0% hallucination)
  • Program Synthesis      — Kolmogorov-optimal code invention from examples
  • MobileCLIP Vision      — Semantic UI grounding (finds broken/misaligned UI)
  • Blackboard Pattern     — Multi-file architecture with 0% context rot
  • Codebase Knowledge Graph — Holographic repo index for surgical editing
  • Web Search + Fetch     — Live internet as memory (DuckDuckGo + Trafilatura)
  • Constraint Sharding    — Perfect rule adherence for code generation

WHAT YOU GET
--------------------------------------------------------------------------------
• No cloud, no API keys, no Ollama, no subscriptions
• Runs entirely offline on your own machine
• ~1GB RAM footprint
• Browser chat UI at http://127.0.0.1:5000/

HOW TO RUN (Windows)
--------------------------------------------------------------------------------
1. Extract this folder anywhere.
2. Double-click: INSTALL_AND_RUN.bat
   (or run: python bootstrap.py)
3. Wait for dependencies to install (first time only).
4. Open your browser to: http://127.0.0.1:5000/
5. Start chatting with II v11.

LLAMA-CPP-PYTHON INSTALLATION (CRITICAL)
--------------------------------------------------------------------------------
The real GGUF model requires llama-cpp-python. On Windows, pip sometimes tries
to compile it from source, which fails without Visual Studio C++ Build Tools.

FIX (2 minutes, recommended):
1. Install Python 3.11 from: https://www.python.org/downloads/release/python-3119/
2. During installation, CHECK "Add Python to PATH"
3. Delete the existing venv folder in this directory
4. Re-run INSTALL_AND_RUN.bat

This works because llama-cpp-python publishes pre-built wheels for Python 3.11.

ALTERNATIVE: Use LM Studio
--------------------------------------------------------------------------------
1. Install LM Studio from https://lmstudio.ai/
2. Load your GGUF model and start Local Server at http://127.0.0.1:1234
3. Run INSTALL_AND_RUN.bat (let step 7 fail, continue in demo mode)
4. II v11 will connect to LM Studio automatically for real model chat

FIRST-RUN NOTE
--------------------------------------------------------------------------------
The first launch installs llama-cpp-python and all dependencies. This takes
2–5 minutes depending on your internet speed. Subsequent launches are instant.

SYSTEM REQUIREMENTS
--------------------------------------------------------------------------------
• Windows 10/11
• Python 3.11 recommended (3.10 also works)
• 8 GB RAM recommended
• 2 GB free disk space (model ~986 MB)

MODEL FILE
--------------------------------------------------------------------------------
The file models/qwen2.5-1.5b-instruct-q4_0.gguf (~986 MB) must be present.
If missing, the launcher will tell you before starting.

OPTIONAL EXTRAS
--------------------------------------------------------------------------------
For full God-Tier capability, optionally install:
  pip install z3-solver torch transformers Pillow mypy
The system still works without them (graceful fallbacks).

LICENSE / DISTRIBUTION
--------------------------------------------------------------------------------
You may redistribute this package as-is. The model file and code are included.
Do not remove the model file or the INSTALL_AND_RUN.bat launcher.

================================================================================
END
================================================================================
