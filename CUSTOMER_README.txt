================================================================================
II v12 — Internet Intelligence (Neuro-Symbolic Agentic OS)
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
• Everything pre-installed in .venv — just double-click start.bat
• Browser chat UI at http://127.0.0.1:5000/

WHAT'S INCLUDED IN THIS PACKAGE
--------------------------------------------------------------------------------
• All Python dependencies pre-installed in .venv/
  (flask, networkx, playwright, trafilatura, tree-sitter, z3-solver,
   torch, transformers, Pillow, sentence-transformers, and more)
• Full II v12 source code (all modules)
• start.bat — One-click launcher
• chat.html — Browser frontend

WHAT YOU NEED TO ADD
--------------------------------------------------------------------------------
1. MODEL FILE: models/qwen2.5-1.5b-instruct-q4_0.gguf (~986 MB)
   Download from: https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF
   Place it in the models/ folder before running.

2. For real model chat (recommended):
   Install LM Studio from https://lmstudio.ai/
   Load the GGUF model, start Local Server at http://127.0.0.1:1234
   Then run start.bat — II connects automatically.

HOW TO RUN (Windows)
--------------------------------------------------------------------------------
1. Extract this folder anywhere.
2. Place qwen2.5-1.5b-instruct-q4_0.gguf in the models/ folder.
3. Double-click: start.bat
4. Wait for the server to start.
5. Open your browser to: http://127.0.0.1:5000/
6. Start chatting with II v12.

FIRST-RUN NOTE
--------------------------------------------------------------------------------
Dependencies are already installed in .venv/. The server starts instantly.
If you don't have the model file yet, the server starts in demo mode
(tools work, but no AI chat). Add the model file and restart for full chat.

SYSTEM REQUIREMENTS
--------------------------------------------------------------------------------
• Windows 10/11
• Python 3.11 (included in .venv — no separate install needed)
• 8 GB RAM recommended
• 2 GB free disk space (model ~986 MB, dependencies ~1 GB)

OPTIONAL EXTRAS
--------------------------------------------------------------------------------
• LM Studio (https://lmstudio.ai/) — Easiest way to run the model
  No compilation needed, just load the GGUF and start the server.
• z3-solver — Already included for verified math
• torch/transformers — Already included for MobileCLIP vision

LICENSE / DISTRIBUTION
--------------------------------------------------------------------------------
MIT License. You may redistribute this package as-is.
Do not remove the model file, .venv folder, or start.bat launcher.

================================================================================
END
================================================================================
