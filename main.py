"""
main.py — Command-line entry point for II v3.0.

The Pure II commands (``ask``, ``solve``, ``interview``, ``brainstorm``,
``hypothesize``, ``memory``) run the deterministic pipeline: live cited
sources, graph traversal and DAG orchestration.  The local model, when one is
configured, is only the renderer.

The remaining commands expose the earlier helpers (repository graph, SMT,
bounded synthesis, the experimental blackboard, repository ingestion).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from graph.ii_graph import CodebaseKnowledgeGraph
from graph.topology import GraphTopologyAnalyzer
from reasoning.verification_cascade import VerificationCascade
from reasoning.ii_smt import Z3Solver, smt_math_tool
from reasoning.ii_synthesis import synthesis_tool
from archive.ii_vision import vision_ground_tool
from blackboard.ii_blackboard import blackboard_tool


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(
        description="II v3.0 - Pure Internet Intelligence (deterministic reasoning; the LLM only renders)"
    )
    parser.add_argument(
        "command",
        choices=[
            "chat",
            "graph",
            "smt",
            "synthesize",
            "vision",
            "blackboard",
            "topology",
            "spectral",  # Deprecated alias for 'topology' (prints a warning).
            "verify",
            "steer",
            "benchmark",
            "ingest",
            "solve",
            "memory",
            "interview",
            "brainstorm",
            "hypothesize",
            "ask",
        ],
        help="Command to run",
    )
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Inline input for the command (alternative to --input)",
    )
    parser.add_argument(
        "--input",
        "-i",
        type=str,
        help="Input string or file path (verify reads a file or stdin)",
    )
    parser.add_argument("--full-cascade", action="store_true", help="Require all injected verification gates; unavailable gates fail closed")
    parser.add_argument("--required-symbol", action="append", default=[], help="Required Python function/class name for verify")
    parser.add_argument("--forbidden-call", action="append", default=[], help="Forbidden call name for verify")
    parser.add_argument("--action", choices=["list", "apply", "optin", "optout", "forget", "show"], default="list", help="steer / memory action")
    parser.add_argument("--vector", help="Named user-supplied steering vector")
    parser.add_argument("--key", help="Memory key (used by: memory forget --key NAME)")
    parser.add_argument("--repo", help="Public GitHub URL for ingest")
    parser.add_argument("--dataset", type=Path, help="Benchmark task JSON")
    parser.add_argument("--responses", type=Path, help="Single-system JSON/JSONL responses")
    parser.add_argument("--raw-responses", type=Path, help="Raw-model JSON/JSONL responses for comparison")
    parser.add_argument("--ii-responses", type=Path, help="II-pipeline JSON/JSONL responses for comparison")
    parser.add_argument("--output", type=Path, help="Benchmark review-packet output path")
    parser.add_argument("--system", default="II", help="Single-system label for benchmark metadata")
    parser.add_argument("--model", default="unspecified", help="Single-system model label for benchmark metadata")
    parser.add_argument("--raw-model", help="Raw-model label for paired comparison")
    parser.add_argument("--ii-model", help="II-pipeline model label for paired comparison")
    parser.add_argument("--settings", default="unspecified", help="Generation settings for benchmark metadata")
    args = parser.parse_args()

    if args.command == "chat":
        from core.ii_agent import run_agent

        prompt = args.query or args.input or input("Enter prompt: ")
        run_agent(prompt)

    elif args.command == "graph":
        print("[MAP] Building repository symbol index...")
        kg = CodebaseKnowledgeGraph(".")
        n = kg.build_graph()
        kg.resolve_cross_file_edges()
        print(f"Parsed {n} files.\n")
        print(kg.get_repository_index()[:2000] + "\n...[truncated]")

    elif args.command in {"topology", "spectral"}:
        if args.command == "spectral":
            print(
                "[DEPRECATED] 'spectral' is a deprecated alias for 'topology'. "
                "The analysis is SCC detection, PageRank and community "
                "clustering -- not eigenvalue-based spectral methods."
            )
        kg = CodebaseKnowledgeGraph(".")
        kg.build_graph()
        analyzer = GraphTopologyAnalyzer(kg.graph)
        print(analyzer.generate_architecture_map())

    elif args.command == "verify":
        input_path = Path(args.query or args.input) if (args.query or args.input) else None
        if input_path is not None and input_path.is_file():
            code = input_path.read_text(encoding="utf-8")
        else:
            code = sys.stdin.read()
        verifier = VerificationCascade()
        if args.full_cascade:
            passed, detail, report = verifier.verify(
                code,
                {
                    "required_symbols": args.required_symbol,
                    "forbidden_calls": args.forbidden_call,
                },
            )
            report["detail"] = detail
        else:
            report = verifier.verify_python(
                code,
                required_symbols=args.required_symbol,
                forbidden_calls=args.forbidden_call,
            ).to_dict()
            passed = report["passed"]
        print(json.dumps(report, indent=2))
        if not passed:
            raise SystemExit(1)

    elif args.command == "smt":
        try:
            Z3Solver()
        except ImportError as exc:
            print(f"[UNAVAILABLE] {exc}")
            return
        print("[INFO] Z3 checks explicit constraints; it does not parse natural-language math.")
        variables = args.query or args.input or input("Variables (e.g., x Int, y Int): ")
        constraints = input("Constraints (e.g., x > 0, y > 0, x + y == 10): ")
        problem = f"VARIABLES: {variables}\nCONSTRAINTS: {constraints}"
        print(smt_math_tool(problem))

    elif args.command == "synthesize":
        print("[SYNTH] Bounded expression search")
        examples = args.query or args.input or input(
            'Examples JSON (e.g., [[[1, 2], 3], [[5, 7], 12]]): '
        )
        print(synthesis_tool(examples))

    elif args.command == "vision":
        print("[VISION] Experimental image/text similarity; it does not localize regions")
        image_path = args.query or args.input or input("Screenshot path (e.g., screenshot.png): ")
        query = input("Query (e.g., misaligned button): ")
        print(vision_ground_tool(image_path, query))

    elif args.command == "blackboard":
        print("[ARCH] Experimental interface store")
        action = input("Action (inspect/list/graph): ").strip().lower()
        if action in ("inspect", "list", "graph"):
            print(blackboard_tool(action, "", "."))
        else:
            print("Unsupported action. Use: inspect, list, or graph")

    elif args.command == "steer":
        print(
            "[ARCHIVED] Activation steering is not part of Pure II. The "
            "experimental steering/probe modules live in archive/ and are not "
            "wired into the runtime."
        )
        raise SystemExit(2)

    elif args.command == "benchmark":
        from tests.benchmarks.eval_runner import (
            build_comparison_packet,
            build_review_packet,
            load_responses,
            load_tasks,
        )

        dataset_path = args.dataset or Path("tests/benchmarks/eval_questions.json")
        output_path = args.output or Path("tests/benchmarks/results/review_packet.json")
        tasks = load_tasks(dataset_path)
        task_ids = {task["id"] for task in tasks}
        comparison_requested = args.raw_responses is not None or args.ii_responses is not None
        if comparison_requested:
            if args.raw_responses is None or args.ii_responses is None or args.responses is not None:
                parser.error("comparison requires both --raw-responses and --ii-responses, without --responses")
            raw_responses = load_responses(args.raw_responses)
            ii_responses = load_responses(args.ii_responses)
            unknown = sorted((set(raw_responses) | set(ii_responses)) - task_ids)
            if unknown:
                parser.error("responses contain unknown task IDs: " + ", ".join(unknown))
            packet = build_comparison_packet(
                tasks,
                raw_responses,
                ii_responses,
                raw_model=args.raw_model or args.model,
                ii_model=args.ii_model or args.model,
                settings=args.settings,
                dataset_hash=hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            )
        else:
            if args.responses is None:
                parser.error("benchmark requires --responses or both comparison response files")
            responses = load_responses(args.responses)
            unknown = sorted(set(responses) - task_ids)
            if unknown:
                parser.error("responses contain unknown task IDs: " + ", ".join(unknown))
            packet = build_review_packet(
                tasks,
                responses,
                system=args.system,
                model=args.model,
                settings=args.settings,
                dataset_hash=hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(packet, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote human-review packet for {len(tasks)} tasks to {output_path}.")
        print("No model inference or automatic factual-correctness score was produced.")

    elif args.command == "ingest":
        if not args.repo:
            parser.error("ingest requires --repo https://github.com/OWNER/REPO")
        from ingestion.repo_ingestor import RepoIngestor
        try:
            print(RepoIngestor().ingest(args.repo))
        except (ValueError, RuntimeError, FileExistsError, PermissionError) as exc:
            print(f"[INGEST FAILED] {exc}")
            raise SystemExit(1)

    elif args.command == "solve":
        from reasoning.logic_tree_solver import LogicTreeSolver

        query = args.query or args.input or input("Question: ")
        result = LogicTreeSolver().solve(query)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        if result.get("status") != "VERIFIED":
            raise SystemExit(2)

    elif args.command == "ask":
        from core.ii_agent import route_task

        prompt = args.query or args.input or input("Prompt: ")
        result = route_task(prompt)
        print(result.get("answer", ""))
        print()
        print(f"[STATUS] {result.get('status')} | intent={result.get('intent')} "
              f"| confidence={result.get('confidence')}")
        if result.get("questions"):
            print("[QUESTIONS]")
            for question in result["questions"]:
                print(f"  - {question['question']}")

    elif args.command == "interview":
        from interaction.interviewer import Interviewer

        prompt = args.query or args.input or input("Task: ")
        interviewer = Interviewer()
        tree = interviewer.build_constraint_tree(prompt)
        asked = interviewer.ask_questions(prompt)
        print(json.dumps({
            "constraint_tree": tree,
            "questions": asked["questions"],
            "proceed_with_defaults": asked["proceed_with_defaults"],
        }, indent=2, ensure_ascii=False))

    elif args.command == "brainstorm":
        from interaction.ii_brainstorming_engine import BrainstormingEngine

        topic = args.query or args.input or input("Topic: ")
        print(BrainstormingEngine().format(topic))

    elif args.command == "hypothesize":
        from invention.combinatorial_hypothesis_engine import (
            CombinatorialHypothesisEngine,
        )

        statement = args.query or args.input or input("Problem statement: ")
        result = CombinatorialHypothesisEngine().test_hypotheses(statement)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))

    elif args.command == "memory":
        from memory.exocortex import Exocortex

        exocortex = Exocortex()
        action = (args.action or "show").lower()
        if action in {"optin", "on", "grant"}:
            print(f"Consent state: {exocortex.set_consent(True)}")
        elif action in {"optout", "off", "revoke"}:
            print(f"Consent state: {exocortex.set_consent(False)}")
        elif action == "forget":
            if args.key:
                print("Forgotten." if exocortex.forget(args.key) else "No such key.")
            else:
                print(f"Forgotten {exocortex.forget_all()} fact(s).")
        else:
            print(json.dumps({
                "description": exocortex.describe(),
                "facts": exocortex.what_i_remember(),
            }, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
