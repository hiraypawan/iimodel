"""
main.py — II v12 CLI Entry Point

Exposes the God-Tier modules from the terminal:
  chat      — Full ReAct agent loop (needs local GGUF model or LM Studio)
  graph     — Holographic codebase index (Python + JS/TS via tree-sitter)
  smt       — Z3 SMT solver (deterministic PhD math)
  synthesize — Enumerative program synthesis (Kolmogorov-optimal)
  vision    — MobileCLIP semantic visual grounding
  blackboard — Interface-first multi-file architecture manager
"""

from __future__ import annotations

import argparse
import json
import sys

from ii_graph import CodebaseKnowledgeGraph
from ii_smt import smt_math_tool
from ii_synthesis import EnumerativeSynthesizer, synthesis_tool
from ii_vision import MobileCLIPGrounder, vision_ground_tool
from ii_blackboard import Blackboard, blackboard_tool
from ii_agent import run_agent_v12


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(
        description="II v12 — Neuro-Symbolic Agentic OS CLI"
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
        ],
        help="Command to run",
    )
    parser.add_argument(
        "--input",
        "-i",
        type=str,
        help="Input string or file path",
    )
    args = parser.parse_args()

    print("[OK] II v12 (Neuro-Symbolic Agentic OS) Initialized")

    if args.command == "chat":
        prompt = args.input or input("Enter prompt: ")
        run_agent_v12(prompt)

    elif args.command == "graph":
        print("[MAP] Building Holographic Index...")
        kg = CodebaseKnowledgeGraph(".")
        n = kg.build_graph()
        kg.resolve_cross_file_edges()
        print(f"Parsed {n} files.\n")
        print(kg.get_holographic_index()[:2000] + "\n...[truncated]")

    elif args.command == "smt":
        print("[MATH] Z3 SMT Solver Active")
        variables = args.input or input("Variables (e.g., x Int, y Int): ")
        constraints = input("Constraints (e.g., x > 0, y > 0, x + y == 10): ")
        problem = f"VARIABLES: {variables}\nCONSTRAINTS: {constraints}"
        print(smt_math_tool(problem))

    elif args.command == "synthesize":
        print("[SYNTH] Enumerative Program Synthesis Active")
        examples = args.input or input(
            'Examples JSON (e.g., [[[1, 2], 3], [[5, 7], 12]]): '
        )
        print(synthesis_tool(examples))

    elif args.command == "vision":
        print("[VISION] MobileCLIP Visual Grounding Active")
        image_path = args.input or input("Screenshot path (e.g., screenshot.png): ")
        query = input("Query (e.g., misaligned button): ")
        print(vision_ground_tool(image_path, query))

    elif args.command == "blackboard":
        print("[ARCH] Blackboard Architecture Active")
        action = input("Action (design/build/inspect/list): ").strip().lower()
        if action == "design":
            prompt = input("System prompt: ")
            board = Blackboard(".")
            arch = BlackboardArchitect(board)
            ids = arch.design_system(prompt, lambda p: p)
            print(f"Designed and locked interfaces: {ids}")
        elif action == "build":
            name = input("Interface ID (e.g., iface_0): ")
            board = Blackboard(".")
            builder = BlackboardBuilder(board)
            result = builder.build_file(name, lambda p: p)
            print(result or "Build failed or interface not locked.")
        elif action == "inspect":
            print(blackboard_tool("inspect", "", "."))
        elif action == "list":
            print(blackboard_tool("list", "", "."))
        else:
            print("Invalid action. Use: design, build, inspect, list")


if __name__ == "__main__":
    main()
