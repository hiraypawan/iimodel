II — LOCAL TOOL-AUGMENTED AGENT PROTOTYPE
==========================================

WHAT THIS IS
------------
II is a Python application that connects an optional local language model to a
Flask chat UI and experimental helper tools. It is an orchestration prototype,
not a new AI model. The repository does not include model weights.

The default example model is Qwen2.5-1.5B-Instruct in GGUF format. You can use
LM Studio as the recommended local backend, or install llama-cpp-python and
provide the GGUF file yourself.

RECOMMENDED SETUP: LM STUDIO
----------------------------
1. Install LM Studio and load a compatible Qwen2.5-1.5B-Instruct GGUF model.
2. Start its OpenAI-compatible local server at http://127.0.0.1:1234.
3. Install Python 3.11 (the Windows launcher targets Python 3.11).
4. In this folder, run:

     py -3.11 -m venv .venv
     .venv\Scripts\activate
     python -m pip install --upgrade pip
     pip install -r requirements.txt
     python app.py

5. Open http://127.0.0.1:5000/.

The server status is shown in the page header and at GET /api/status. If no
model backend is connected, the app starts in demo mode. Demo mode does not
produce AI answers; it displays instructions for connecting a backend.

WINDOWS LAUNCHER
----------------
Double-click start.bat to create a virtual environment, install dependencies,
and start the server. It does not download model weights. INSTALL_AND_RUN.bat
and setup_ii.bat call the same launcher.

OPTIONAL: DOWNLOAD GGUF FOR DIRECT LOCAL INFERENCE
--------------------------------------------------
Model repository:
https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF

Direct Q4_0 file:
https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_0.gguf?download=true

Save the file as:
models\qwen2.5-1.5b-instruct-q4_0.gguf

Then install llama-cpp-python in the same Python environment:

     python -m pip install llama-cpp-python

The model file is large and is excluded from Git. Check the model repository for
its license and usage terms.

CURRENT LIMITATIONS
-------------------
- The repository index is a basic symbol/import/call index, not semantic or data-flow search; graph summaries are approximate navigation hints. A synthetic cycle fixture tests detection but is not an AI benchmark.
- Repository ingestion accepts public GitHub URLs via CLI/API, uses a blocking local clone, and checks a supported root license set. Cloned code is parsed, not executed; cloning is not sandboxed and the API is unauthenticated.
- The static verification endpoint does not run code or prove correctness.
- Z3 solving requires a separate install and explicit formal constraints; it
  does not translate natural-language math into formulas.
- Example synthesis is limited to a small expression set.
- Steering/probe tools ship no model-specific vectors, and KV paging is metadata-only; current backends do not expose the required hooks/cache APIs.
- The component catalog contains ten original Flask/Jinja candidates, all unapproved. No renderer/build pipeline is implemented.
- Web search and URL fetching contact external services and require internet.
- Code execution is not sandboxed and runs with the current user's permissions.
- Vision localization is not implemented. Blackboard can return an approximate dependency graph; design/build automation is not a supported end-to-end feature.
- No comparative AI benchmark results are published.

PRIVACY AND SAFETY
------------------
Inference through LM Studio or llama-cpp-python can stay on the local machine.
Web search and URL fetching make external requests. Do not expose the development
server or code-execution features to untrusted users.

The project code is MIT-licensed; see LICENSE. Model weights have separate terms.
