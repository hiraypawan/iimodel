# II v2.0 limitations — honest assessment

## Model, memory, and inference

- II is an orchestration prototype around an existing language model, not a new
  foundation model or training system. No fine-tuning or base-weight editing is
  performed.
- Model weights are not included. The optional GGUF filename is
  `models/qwen2.5-1.5b-instruct-q4_0.gguf`; LM Studio is another optional backend.
- Real GGUF inference and live LM Studio inference have not been validated in
  this repository's test run. Without a model backend, the app is in demo mode
  and does not generate model answers.
- Peak RAM has not been measured. There is no verified under-2-GB guarantee;
  results depend on model quantization, context length, backend, and platform.
- No MMLU, GSM8K, HumanEval, SWE-bench, BFCL, or head-to-head Astra/Fable result
  is published. No scalar capability rating is implied.

## Graphs, repository ingestion, and the cycle fixture

- The indexer uses AST/tree-sitter plus approximate import/call resolution.
  Dynamic imports, package semantics, ambiguous symbols, generated files, and
  many language constructs can be missed.
- SCCs, PageRank, and file clusters are structural navigation hints, not proof of
  architecture or semantic importance. The `spectral` command is a compatibility
  alias; these algorithms are not eigenvalue-based spectral analysis.
- A synthetic 30-file import-cycle fixture tests deterministic cycle detection.
  It is not an AI benchmark, does not compare raw Qwen with II, and does not show
  that a model can repair the cycle.
- Repository ingestion accepts HTTPS GitHub owner/repo URLs through CLI and
  API, applies a conservative root-license filter, and parses cloned files
  without executing them. The API clone is blocking and capped at 60 seconds and
  5,000 supported files. The filter is heuristic—not legal advice, a complete
  SPDX parser, or a substitute for human review. Cache data is metadata; it does
  not establish provenance beyond recorded fields.
- Ingestion invokes a local `git clone` subprocess; parsing source as data does
  not make that subprocess an OS-level sandbox. The API has no authentication
  and should not be exposed to untrusted clients.

## Verification and reasoning

- Default verification checks Python syntax, caller-requested names, and a small
  heuristic risk list. It does not run code, run a type checker, prove semantics,
  or establish security.
- Full Gate 2 requires an external test runner and case generator; Gate 3 requires
  an external yes/no entailment adapter. Missing gates are marked skipped and
  fail the full cascade. The code cannot certify that a caller's runner is
  isolated or that a model judge is reliable.
- Gate outputs are fallible and are not statistically independent. No alpha
  values, multiplied false-positive estimates, or success-rate claims are
  supported by measurements.
- Z3 solves supported, explicitly encoded formulas. A wrong or incomplete
  encoding can produce a misleading result. Natural-language decomposition is
  heuristic, and translation only occurs through a caller-supplied callback;
  semantic fidelity is not established.
- Synthesis searches a small expression set. A match to examples is not proof of
  correctness on unseen inputs.
- `execute_code` and optional local subprocess checks run with the current OS
  user's permissions. A timeout is not isolation or a sandbox.

## Steering, probes, constrained decoding, and KV cache

- Contrastive vectors and PyTorch hooks are optional generic utilities. No
  model-specific vectors are bundled; valid directions require model-specific
  activation extraction and evaluation.
- Proposed sarcasm/frustration/urgency/confusion probes are not bundled or
  calibrated. A linear score is not evidence of user emotion or intent.
- The shipped GGUF and LM Studio adapters do not expose the portable hidden-state,
  logit, or KV-state APIs required for the middle-layer and entropy-search
  experiments. Entropy search is an adapter interface, not an active chat feature.
- No constrained-decoding/FSM integration is installed or wired to the current
  backends. Syntax validity is not guaranteed during generation.
- The 64-token KV module stores min/max page summaries and ranks candidates. It
  does not transfer cache blocks to/from RAM or VRAM, extend context, or modify
  llama.cpp internals; these calls raise `NotImplementedError`.
- TIES/DARE functions operate on optional NumPy arrays and return new arrays.
  They do not read or alter model weights, and are not integrated into inference.

## UI catalog and evidence pipeline

- Ten original Flask/Jinja component fragments are marked `candidate`; none is
  `approved`. License/build/unit/a11y/visual-review fields remain pending and no
  renderer or generated-project build pipeline exists.
- Extracted web passages preserve source URLs but may omit context, be stale, or
  be incorrectly extracted. Domain metadata is heuristic, not reputation.
- Numeric/negation disagreement flags are lexical signals for review, not robust
  contradiction resolution. An `insufficient_evidence` status means the simple
  retrieval heuristic found no matching passage; it does not establish that no
  evidence exists elsewhere.

## Evaluation, privacy, and cost

The checked-in benchmark dataset is empty until reviewed tasks, ground truth, and
sources are supplied. The harness creates mechanical-signal and human-review
packets, including paired raw-vs-II response records; it does not run model
inference or automatically decide factual correctness, citation support, or
abstention quality. No benchmark score has been produced.

Web search and URL fetching contact external services/sites. Local inference can
stay on-device when configured locally and when web tools are not used, but the
whole application is not air-gapped, universally free, or guaranteed private.
The development server has no authentication and should remain on a trusted
local machine.
