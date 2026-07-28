"""Evidence-driven autonomy governor for one-touch VOID OS operation.

The governor observes the local project, maintains its own backlog, selects the
highest-value bounded task, and records lessons. It never asks the user to plan.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


class AutonomyGovernor:
    def __init__(self, cfg: dict, router, state_root: Path):
        self.cfg = cfg
        self.router = router
        self.state_root = Path(state_root)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.backlog_path = self.state_root / "living_backlog.json"
        self.lessons_path = self.state_root / "lessons.jsonl"

    def choose_task(self, objective: str, context: str, recent_tasks: list[str]) -> str:
        candidates = self._observe(context)
        history = self._load_backlog()
        recent_lower = " ".join(recent_tasks).lower()
        for item in candidates:
            if item["task"].lower() in recent_lower:
                item["score"] -= 35
            prior = history.get(item["key"], {})
            item["score"] -= min(25, int(prior.get("attempts", 0)) * 5)
            item["score"] += min(15, int(prior.get("successes", 0)) * 3)
        candidates.sort(key=lambda x: (-x["score"], x["key"]))
        selected = candidates[0]
        history.setdefault(selected["key"], {"attempts": 0, "successes": 0})
        history[selected["key"]]["attempts"] += 1
        history[selected["key"]]["last_selected"] = int(time.time())
        history[selected["key"]]["task"] = selected["task"]
        history[selected["key"]]["evidence"] = selected["evidence"]
        self._save_backlog(history)
        return selected["task"]

    def record_outcome(self, task: str, output_path: str, success: bool, summary: str = "") -> None:
        record = {
            "time": int(time.time()),
            "task": task,
            "output": output_path,
            "success": bool(success),
            "summary": summary[:1200],
        }
        with self.lessons_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        history = self._load_backlog()
        for item in history.values():
            if item.get("task") == task and success:
                item["successes"] = int(item.get("successes", 0)) + 1
        self._save_backlog(history)

    def _observe(self, context: str) -> list[dict]:
        lower = context.lower()
        candidates = []
        def add(key: str, task: str, score: int, evidence: str):
            candidates.append({"key": key, "task": task, "score": score, "evidence": evidence})

        error_count = sum(lower.count(x) for x in ("traceback", "error:", "exception", "failed", "winerror"))
        todo_count = lower.count("todo") + lower.count("fixme")
        test_mentions = lower.count("test")
        if error_count:
            add("repair_failures", "Identify the most repeated verified failure, implement or specify the smallest safe fix, and produce a regression test or exact verification procedure.", 100 + min(error_count, 20), f"{error_count} failure markers")
        if todo_count:
            add("resolve_todo", "Resolve the highest-impact TODO or FIXME supported by the project evidence and verify that the surrounding behavior still works.", 82 + min(todo_count, 15), f"{todo_count} TODO markers")
        if test_mentions < 4:
            add("test_coverage", "Create the most valuable missing automated regression test for a critical existing behavior, prioritizing START, STOP, rollback, path safety, and recovery.", 80, "little visible test coverage")
        if "readme" not in lower or "start" not in lower or "stop" not in lower:
            add("operator_docs", "Make the operator experience truly one-touch by improving startup diagnostics and the concise START/STOP documentation without adding planning controls.", 62, "operator guidance appears incomplete")
        if "__pycache__" in lower or "outputs" in lower:
            add("context_hygiene", "Reduce noisy or duplicated project context so autonomous decisions are grounded in current source, configuration, failures, and tests rather than generated artifacts.", 66, "generated artifacts are visible in context")
        add("reasoning_quality", "Improve one measurable part of autonomous task selection, evidence checking, or result verification while preserving the two-button interface.", 58, "standing improvement objective")
        add("creative_alternatives", "Generate three materially different solutions to one verified project bottleneck, compare them, and deliver the safest high-value option with a verification method.", 55, "standing creative problem-solving objective")
        add("reliability", "Inspect the current local architecture for one reliability bottleneck and produce a bounded, reversible improvement with a clear pass or fail check.", 54, "standing reliability objective")
        return candidates

    def _load_backlog(self) -> dict:
        try:
            value = json.loads(self.backlog_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_backlog(self, value: dict) -> None:
        self.backlog_path.write_text(json.dumps(value, indent=2), encoding="utf-8")
