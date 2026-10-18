"""JSON-backed checkpoints, state facts, decisions, and scratchpad storage.

Files are stored in the configured work directory. This module does not
provide cross-device synchronization, model memory, or privacy guarantees.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Checkpoint:
    episode_id: str
    step: int
    timestamp: str
    summary: str
    facts: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)  # file paths created/modified


@dataclass
class DecisionRecord:
    decision_hash: str
    concerns: str
    timestamp: str
    rules_checked: list[str] = field(default_factory=list)
    rules_passed: list[str] = field(default_factory=list)


class StateManager:
    """
    Persistent state manager for II agent sessions.

    Directory layout (under <workdir>/.ii_state/):
      checkpoints/   — one JSON per checkpoint episode
      state_ledger.json   — append-only fact ledger
      decision_ledger.json — append-only reasoning ledger
      scratchpad.txt — externalized chain-of-thought
      constraints.json — constraint sharding results
    """

    def __init__(self, workdir: str | Path = ".", state_dir: str | Path = ".ii_state"):
        self.workdir = Path(workdir).resolve()
        self.state_dir = self.workdir / state_dir
        self.checkpoints_dir = self.state_dir / "checkpoints"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

        self.state_ledger_path = self.state_dir / "state_ledger.json"
        self.decision_ledger_path = self.state_dir / "decision_ledger.json"
        self.scratchpad_path = self.state_dir / "scratchpad.txt"
        self.constraints_path = self.state_dir / "constraints.json"

        # In-memory caches
        self._state_ledger: list[dict[str, Any]] = self._load_json_list(
            self.state_ledger_path
        )
        self._decision_ledger: list[dict[str, Any]] = self._load_json_list(
            self.decision_ledger_path
        )
        self._constraints: dict[str, Any] = self._load_json_dict(
            self.constraints_path
        )

    # ------------------------------------------------------------------ #
    # Checkpoints
    # ------------------------------------------------------------------ #
    def save_checkpoint(
        self,
        episode_id: str,
        step: int,
        summary: str,
        facts: dict[str, Any] | None = None,
        artifacts: list[str] | None = None,
    ) -> str:
        """Write a checkpoint file and append the fact to state_ledger."""
        cp = Checkpoint(
            episode_id=episode_id,
            step=step,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            summary=summary,
            facts=facts or {},
            artifacts=artifacts or [],
        )
        cp_path = self.checkpoints_dir / f"{episode_id}_step{step}.json"
        cp_path.write_text(json.dumps(asdict(cp), indent=2), encoding="utf-8")

        # Append to state ledger
        self._state_ledger.append(
            {
                "type": "checkpoint",
                "episode_id": episode_id,
                "step": step,
                "timestamp": cp.timestamp,
                "summary": summary,
                "facts": facts or {},
            }
        )
        self._save_json_list(self.state_ledger_path, self._state_ledger)
        return str(cp_path)

    def load_checkpoint(self, episode_id: str, step: int) -> Checkpoint | None:
        cp_path = self.checkpoints_dir / f"{episode_id}_step{step}.json"
        if not cp_path.exists():
            return None
        data = json.loads(cp_path.read_text(encoding="utf-8"))
        return Checkpoint(**data)

    def list_checkpoints(self, episode_id: str | None = None) -> list[Checkpoint]:
        out: list[Checkpoint] = []
        for cp_file in self.checkpoints_dir.glob("*.json"):
            data = json.loads(cp_file.read_text(encoding="utf-8"))
            if episode_id is None or data.get("episode_id") == episode_id:
                out.append(Checkpoint(**data))
        out.sort(key=lambda c: (c.episode_id, c.step))
        return out

    # ------------------------------------------------------------------ #
    # Dual ledger
    # ------------------------------------------------------------------ #
    def append_state_fact(self, key: str, value: Any, source: str = "agent") -> None:
        """Append an immutable fact to the state ledger."""
        entry = {
            "type": "fact",
            "key": key,
            "value": value,
            "source": source,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._state_ledger.append(entry)
        self._save_json_list(self.state_ledger_path, self._state_ledger)

    def get_state_fact(self, key: str) -> Any:
        for entry in reversed(self._state_ledger):
            if entry.get("type") == "fact" and entry.get("key") == key:
                return entry.get("value")
        return None

    def record_decision(
        self,
        concerns: str,
        rules_checked: list[str] | None = None,
        rules_passed: list[str] | None = None,
    ) -> str:
        """
        Create a Decision Hash: a 50-token summary of concerns/reasoning
        injected on context reset to prevent State-Isolation Amnesia.
        """
        payload = {
            "concerns": concerns,
            "rules_checked": rules_checked or [],
            "rules_passed": rules_passed or [],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        decision_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()[:16]

        record = DecisionRecord(
            decision_hash=decision_hash,
            concerns=concerns,
            timestamp=payload["timestamp"],
            rules_checked=rules_checked or [],
            rules_passed=rules_passed or [],
        )
        self._decision_ledger.append(asdict(record))
        self._save_json_list(self.decision_ledger_path, self._decision_ledger)
        return decision_hash

    def get_latest_decision_hash(self) -> str | None:
        for entry in reversed(self._decision_ledger):
            if entry.get("type") == "decision" or "decision_hash" in entry:
                return entry.get("decision_hash")
        return None

    def get_decision_context(self) -> str:
        """Return a compact decision-context block for prompt injection."""
        if not self._decision_ledger:
            return "No prior decisions recorded."
        latest = self._decision_ledger[-1]
        lines = [
            "=== DECISION LEDGER (LAST DECISION) ===",
            f"Decision Hash: {latest.get('decision_hash')}",
            f"Concerns: {latest.get('concerns')}",
            f"Rules Checked: {', '.join(latest.get('rules_checked', []))}",
            f"Rules Passed: {', '.join(latest.get('rules_passed', []))}",
            f"Timestamp: {latest.get('timestamp')}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Scratchpad with rolling-window summarization
    # ------------------------------------------------------------------ #
    def append_scratchpad(self, text: str) -> None:
        """Append a thought step to the externalized scratchpad."""
        with open(self.scratchpad_path, "a", encoding="utf-8") as f:
            f.write(text + "\n---\n")

    def get_scratchpad_window(self, max_chars: int = 1500) -> str:
        """
        Return the most recent scratchpad content within max_chars.
        Older content is implicitly summarized away (rolling window).
        """
        if not self.scratchpad_path.exists():
            return ""
        content = self.scratchpad_path.read_text(encoding="utf-8")
        if len(content) <= max_chars:
            return content
        # Keep the tail; drop the head (oldest thoughts)
        return "...[older thoughts summarized away]...\n" + content[-max_chars:]

    def clear_scratchpad(self) -> None:
        if self.scratchpad_path.exists():
            self.scratchpad_path.write_text("", encoding="utf-8")

    # ------------------------------------------------------------------ #
    # Constraint sharding ledger
    # ------------------------------------------------------------------ #
    def record_constraint_result(
        self, rule_id: str, passed: bool, detail: str = ""
    ) -> None:
        key = f"constraint:{rule_id}"
        self._constraints[key] = {
            "rule_id": rule_id,
            "passed": passed,
            "detail": detail,
            "last_checked": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
        }
        self._save_json_dict(self.constraints_path, self._constraints)

    def get_failed_constraints(self) -> list[dict[str, Any]]:
        return [
            v for v in self._constraints.values() if not v.get("passed", True)
        ]

    def get_constraint_summary(self) -> str:
        if not self._constraints:
            return "No constraints tracked yet."
        total = len(self._constraints)
        passed = sum(1 for v in self._constraints.values() if v.get("passed"))
        failed = total - passed
        lines = [f"Constraints: {passed}/{total} passed, {failed} failed"]
        for v in self._constraints.values():
            if not v.get("passed"):
                lines.append(f"  FAIL {v['rule_id']}: {v.get('detail', '')}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Optional inference telemetry caches (bookkeeping only)
    # ------------------------------------------------------------------ #
    def record_negative_outcome(
        self,
        state: str,
        action: str,
        error: str,
        penalty: float = 1.0,
    ) -> str:
        """Store a failed state/action/error tuple for future adapter use.

        This cache is not connected to llama.cpp logits or LM Studio decoding;
        it is persisted feedback metadata, not an active logit-bias mechanism.
        """
        if not isinstance(penalty, (int, float)) or not math.isfinite(float(penalty)) or penalty < 0:
            raise ValueError("penalty must be a finite non-negative number.")
        record = {
            "type": "negative_logit_bias_cache",
            "state_hash": hashlib.sha256(state.encode("utf-8")).hexdigest(),
            "state_excerpt": state[:500],
            "action": action[:300],
            "error": error[:1000],
            "penalty": float(penalty),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        key = hashlib.sha256(
            f"{record['state_hash']}|{record['action']}|{record['error']}".encode("utf-8")
        ).hexdigest()[:16]
        record["record_id"] = key
        self._state_ledger.append(record)
        self._save_json_list(self.state_ledger_path, self._state_ledger)
        return key

    def get_negative_logit_bias_cache(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent failure records; values are not applied to model logits."""
        if limit <= 0:
            return []
        records = [
            entry for entry in self._state_ledger
            if entry.get("type") == "negative_logit_bias_cache"
        ]
        return records[-limit:]

    def record_task_vector_activation(
        self, name: str, coefficient: float, *, applied: bool = False
    ) -> None:
        """Record requested activation metadata; applied means a hook was installed."""
        if not math.isfinite(float(coefficient)):
            raise ValueError("coefficient must be finite.")
        self._state_ledger.append({
            "type": "task_vector_activation",
            "name": name,
            "coefficient": float(coefficient),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "applied": bool(applied),
        })
        self._save_json_list(self.state_ledger_path, self._state_ledger)

    def record_kv_page(self, page_id: str, tier: str) -> None:
        """Record a requested page tier; does not move/cache model KV tensors."""
        self._state_ledger.append({
            "type": "kv_page_index",
            "page_id": page_id,
            "tier": tier,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "transfer_performed": False,
        })
        self._save_json_list(self.state_ledger_path, self._state_ledger)

    def get_kv_page_index(self, limit: int = 100) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        records = [entry for entry in self._state_ledger if entry.get("type") == "kv_page_index"]
        return records[-limit:]

    def get_task_vector_history(self, limit: int = 100) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        records = [
            entry for entry in self._state_ledger
            if entry.get("type") == "task_vector_activation"
        ]
        return records[-limit:]

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _load_json_list(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _save_json_list(self, path: Path, data: list[dict[str, Any]]) -> None:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load_json_dict(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_json_dict(self, path: Path, data: dict[str, Any]) -> None:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")


if __name__ == "__main__":
    import sys
    sm = StateManager(sys.argv[1] if len(sys.argv) > 1 else ".")
    sm.append_state_fact("project_name", "II-Agent")
    sm.record_decision(
        concerns="Model may hallucinate package names; verify via pip.",
        rules_checked=["imports_valid", "syntax_ok"],
        rules_passed=["syntax_ok"],
    )
    sm.append_scratchpad("Thinking about how to structure the web search tool...")
    print("State initialized at", sm.state_dir)
    print(sm.get_decision_context())