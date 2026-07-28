"""Autonomous implementation controller for evidence-backed project upgrades.

Turns an autopilot deliverable into a bounded code proposal, applies only changes
that pass a conservative local policy, validates them, and relies on CodingStudio
for atomic writes, backups, rollback, and audit logging.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..kernel.paths import DATA
from ..kernel.witness import log


class AutonomousImplementationController:
    """Implement low-risk project upgrades without asking the operator to plan."""

    def __init__(self, cfg: dict, coding_studio):
        self.cfg = cfg
        self.coding = coding_studio
        self.state_dir = DATA / "autonomous_implementation"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def enabled(self) -> bool:
        return bool(self.cfg.get("autopilot", {}).get("implement_project_upgrades", True))

    def run_once(self, task: str, evidence: str, deliverable: str) -> dict:
        if not self.enabled():
            return self._finish({"status": "DISABLED", "task": task})

        # An empty project is a valid starting state.  Earlier builds skipped
        # implementation when ``code/`` had no files, which meant a fresh
        # project could never bootstrap itself.  CodingStudio already supports
        # creating new files from an empty context, so let the proposal engine
        # produce the first bounded implementation.
        available = self.coding.list_files("project")
        selected = self._select_files(task, available) if available else []
        instruction = (
            "Implement the useful upgrade described below in the active project. "
            "Make one bounded, reversible, low-risk change grounded in the supplied source and evidence. "
            "Preserve existing behavior unless the task explicitly repairs it. Add or update a regression test "
            "when practical. Do not add dependencies, networking, shell execution, secret access, persistence, "
            "permission changes, destructive file operations, or files outside the approved project root. "
            "Modify no more than three files. Return complete replacement files.\n\n"
            f"TASK:\n{task}\n\n"
            f"PROJECT EVIDENCE:\n{evidence[:12000]}\n\n"
            f"AUTOPILOT DELIVERABLE:\n{deliverable[:12000]}"
        )
        proposal = self.coding.propose(instruction, target="project", selected_files=selected)
        decision = self._assess(proposal)
        record = {
            "proposal_id": proposal.get("id"),
            "created_at": int(time.time()),
            "task": task,
            "decision": decision,
            "files": [f.get("path") for f in proposal.get("files", [])],
            "summary": proposal.get("summary", ""),
        }
        if decision["auto_apply"]:
            try:
                result = self.coding.apply(proposal["id"])
                record.update({
                    "status": "APPLIED",
                    "backup": result.get("backup"),
                    "validation": result.get("checks", {}),
                })
                log("autonomous_project_upgrade_applied", record)
            except Exception as exc:
                record.update({"status": "FAILED_ROLLED_BACK", "error": str(exc)})
                log("autonomous_project_upgrade_failed", record)
        else:
            record["status"] = "REJECTED"
            log("autonomous_project_upgrade_rejected", record)
        return self._finish(record)

    def _select_files(self, task: str, available: list[str]) -> list[str]:
        words = {w.lower() for w in str(task).replace("_", " ").split() if len(w) > 3}
        ranked = []
        for rel in available:
            lower = rel.lower()
            score = sum(2 for w in words if w in lower)
            if lower.startswith("tests/") or "/tests/" in lower:
                score += 2
            if lower.endswith(("readme.md", "config.json")):
                score += 1
            ranked.append((score, rel))
        return [rel for _, rel in sorted(ranked, key=lambda item: (-item[0], item[1]))[:12]]

    def _assess(self, proposal: dict) -> dict:
        files = proposal.get("files", [])
        paths = [str(item.get("path", "")).replace("\\", "/") for item in files]
        reasons = []
        if str(proposal.get("risk", "")).lower() != "low":
            reasons.append("proposal risk is not low")
        if not files or len(files) > 3:
            reasons.append("proposal must change one to three files")
        for path in paths:
            lower = path.lower()
            if lower.startswith(("data/", ".git/")):
                reasons.append(f"runtime or repository metadata path blocked: {path}")
            if lower.endswith(("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg")):
                reasons.append(f"dependency or packaging file blocked: {path}")
        combined = "\n".join(str(item.get("content", "")) for item in files).lower()
        forbidden = (
            "subprocess.popen", "os.system(", "shell=true", "pip install", "requests.",
            "urllib.request", "socket.", "winreg", "powershell", "cmd.exe", "api_token",
            "data/secrets", "chmod(", "eval(", "exec(", "shutil.rmtree", "os.remove(",
        )
        for token in forbidden:
            if token in combined:
                reasons.append(f"forbidden capability marker: {token}")
        return {"auto_apply": not reasons, "reasons": reasons or ["low-risk implementation policy passed"]}

    def _finish(self, record: dict) -> dict:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = self.state_dir / f"implementation_{stamp}_{time.time_ns() % 1000000:06d}.json"
        path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        return record
