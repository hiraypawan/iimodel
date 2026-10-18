"""Create a reproducible, human-reviewable evaluation packet.

This utility does not call a model and deliberately does not auto-score factual
correctness. It only checks basic response presence, URL presence, and exact
matches against the task's listed gold URLs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

HERE = Path(__file__).resolve().parent
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
ABSTENTION_RE = re.compile(
    r"\b(insufficient evidence|cannot determine|can't determine|could not verify|"
    r"unable to verify|not enough information|i don't know)\b",
    re.IGNORECASE,
)


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalise_url(url: str) -> str:
    return url.rstrip(".,;:!?)]").rstrip("/").lower()


def load_tasks(dataset_path: Path) -> list[dict]:
    tasks = _read_json(dataset_path)
    schema = _read_json(HERE / "eval_questions.schema.json")
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(tasks),
        key=lambda error: list(error.path),
    )
    if errors:
        raise ValueError("Invalid dataset: " + "; ".join(error.message for error in errors))
    ids = [task["id"] for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("Dataset task IDs must be unique.")
    return tasks


def load_responses(path: Path) -> dict[str, str]:
    responses: dict[str, str] = {}
    if path.suffix.lower() == ".jsonl":
        records = []
        with path.open(encoding="utf-8") as file:
            for line_number, line in enumerate(file, 1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON on response line {line_number}: {exc.msg}") from exc
    else:
        records = _read_json(path)
        if not isinstance(records, list):
            raise ValueError("Response JSON must be an array or a .jsonl file.")

    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("task_id"), str):
            raise ValueError("Each response must contain a string task_id.")
        if not isinstance(record.get("response"), str):
            raise ValueError(f"Response for task {record['task_id']!r} must be a string.")
        task_id = record["task_id"]
        if task_id in responses:
            raise ValueError(f"Duplicate response for task {task_id!r}.")
        responses[task_id] = record["response"]
    return responses


def build_review_packet(
    tasks: list[dict],
    responses: dict[str, str],
    *,
    system: str,
    model: str,
    settings: str,
    dataset_hash: str | None = None,
) -> dict:
    results = []
    for task in tasks:
        response = responses.get(task["id"], "")
        cited_urls = [_normalise_url(url) for url in URL_RE.findall(response)]
        gold_urls = [_normalise_url(url) for url in task["gold_sources"]]
        results.append(
            {
                "task_id": task["id"],
                "category": task["category"],
                "answerable": task["answerable"],
                "response_present": bool(response.strip()),
                "citation_present": bool(cited_urls),
                "gold_source_url_matched": any(url in cited_urls for url in gold_urls),
                "abstention_phrase_detected": bool(ABSTENTION_RE.search(response)),
                "response": response,
                "reference_claims": task["reference_claims"],
                "reference_answer": task.get("reference_answer"),
                "ground_truth": task.get("ground_truth", task.get("reference_answer")),
                "difficulty": task.get("difficulty", "unspecified"),
                "gold_sources": task["gold_sources"],
                "manual_review": {
                    "factual_correctness": None,
                    "citation_support": None,
                    "abstention_appropriate": None,
                    "reviewer": None,
                    "notes": None,
                    "status": "pending",
                },
            }
        )

    return {
        "schema_version": "1.0",
        "run": {
            "system": system,
            "model": model,
            "settings": settings,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dataset_sha256": dataset_hash or _canonical_hash(tasks),
        },
        "mechanical_signals_only": True,
        "human_review_required": True,
        "results": results,
    }


def build_comparison_packet(
    tasks: list[dict],
    raw_model_responses: dict[str, str],
    ii_responses: dict[str, str],
    *,
    raw_model: str,
    ii_model: str,
    settings: str,
    dataset_hash: str | None = None,
) -> dict:
    """Create a blinded-review-friendly raw-vs-II packet with no auto-score."""
    raw_packet = build_review_packet(
        tasks,
        raw_model_responses,
        system="raw_model",
        model=raw_model,
        settings=settings,
        dataset_hash=dataset_hash,
    )
    ii_packet = build_review_packet(
        tasks,
        ii_responses,
        system="II_v2_pipeline",
        model=ii_model,
        settings=settings,
        dataset_hash=dataset_hash,
    )
    raw_by_id = {item["task_id"]: item for item in raw_packet["results"]}
    ii_by_id = {item["task_id"]: item for item in ii_packet["results"]}
    results = []
    for task in tasks:
        task_id = task["id"]
        results.append({
            "task_id": task_id,
            "category": task["category"],
            "difficulty": task.get("difficulty", "unspecified"),
            "raw_model": raw_by_id[task_id],
            "ii_v2": ii_by_id[task_id],
            "manual_comparison": {
                "preferred_response": None,
                "factual_correctness_difference": None,
                "citation_support_difference": None,
                "abstention_difference": None,
                "reviewer": None,
                "notes": None,
                "status": "pending",
            },
        })
    return {
        "schema_version": "1.0",
        "run": {
            "raw_model": raw_model,
            "ii_model": ii_model,
            "settings": settings,
            "dataset_sha256": dataset_hash or _canonical_hash(tasks),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "results": results,
        "mechanical_signals_only": True,
        "human_review_required": True,
        "automatic_quality_score": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--system", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--settings", default="unspecified")
    args = parser.parse_args()

    tasks = load_tasks(args.dataset)
    responses = load_responses(args.responses)
    unknown = sorted(set(responses) - {task["id"] for task in tasks})
    if unknown:
        raise SystemExit("Responses contain unknown task IDs: " + ", ".join(unknown))

    packet = build_review_packet(
        tasks,
        responses,
        system=args.system,
        model=args.model,
        settings=args.settings,
        dataset_hash=hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote review packet for {len(tasks)} tasks to {args.output}.")
    if not tasks:
        print("Dataset is empty; no benchmark score was produced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
