"""Bounded self-upgrade controller for VOID OS.

Autopilot may propose and apply only low-risk, reversible core changes. Every
change is backed up, compiled, tested, logged, and rolled back on failure.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..kernel.paths import BASE, DATA
from ..kernel.witness import log


class SelfUpgradeController:
    PROTECTED = {
        "void_os/kernel/security.py",
        "void_os/coding/studio.py",
        "void_os/autopilot/self_upgrade.py",
        "boot.py",
        "VERIFY_SECURITY.py",
        "CORE_MANIFEST_SHA256.json",
        "data/secrets/api_token.txt",
    }

    def __init__(self, cfg: dict, coding_studio):
        self.cfg = cfg
        self.coding = coding_studio
        self.state_dir = DATA / "self_upgrade"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def enabled(self) -> bool:
        return bool(self.cfg.get("autopilot", {}).get("upgrade_void_os", True))

    def should_run(self, cycle: int) -> bool:
        every = max(1, int(self.cfg.get("autopilot", {}).get("core_upgrade_every_cycles", 3)))
        return self.enabled() and cycle % every == 0

    def run_once(self, task: str, evidence: str) -> dict:
        selected = self._select_context_files(task)
        instruction = (
            "Improve VOID OS itself with one small, measurable, low-risk change. "
            "Use the project evidence below. Preserve START/STOP simplicity, local-only behavior, "
            "security checks, backups, rollback, and existing features. Do not add dependencies, "
            "permissions, networking, shell execution, startup persistence, or secret access. "
            "Prefer reliability, reasoning quality, prompt efficiency, diagnostics, or tests. "
            "Modify no more than two files.\n\n"
            f"CURRENT TASK: {task}\n\nEVIDENCE:\n{evidence[:12000]}"
        )
        proposal = self.coding.propose(instruction, target="core", selected_files=selected)
        decision = self._assess(proposal)
        record = {
            "proposal_id": proposal.get("id"),
            "created_at": int(time.time()),
            "decision": decision,
            "files": [f.get("path") for f in proposal.get("files", [])],
            "summary": proposal.get("summary", ""),
        }
        if decision["auto_apply"]:
            result = self.coding.apply(proposal["id"])
            record.update({
                "status": "APPLIED",
                "backup": result.get("backup"),
                "validation": result.get("checks", {}),
                "restart_required": result.get("restart_required", False),
            })
            log("self_upgrade_applied", record)
        else:
            record["status"] = "REJECTED"
            log("self_upgrade_rejected", record)
        self._save_record(record)
        return record

    def _select_context_files(self, task: str) -> list[str]:
        files = self.coding.list_files("core")
        preferred = [
            "void_os/ui/app.py",
            "void_os/agents/runtime.py",
            "void_os/models/router.py",
            "void_os/workflows/engine.py",
            "config.json",
        ]
        words = {w.lower() for w in str(task).replace("_", " ").split() if len(w) > 3}
        ranked = []
        for rel in files:
            score = sum(1 for w in words if w in rel.lower())
            if rel in preferred:
                score += 3
            if rel in self.PROTECTED:
                score -= 100
            ranked.append((score, rel))
        return [rel for _, rel in sorted(ranked, reverse=True)[:4]]

    def _assess(self, proposal: dict) -> dict:
        files = proposal.get("files", [])
        paths = [str(item.get("path", "")).replace("\\", "/") for item in files]
        reasons = []
        if str(proposal.get("risk", "")).lower() != "low":
            reasons.append("proposal risk is not low")
        if not files or len(files) > 2:
            reasons.append("proposal must change one or two files")
        for path in paths:
            if path in self.PROTECTED:
                reasons.append(f"protected path: {path}")
            if path.startswith("data/") or path.startswith("datasets/"):
                reasons.append(f"runtime data path blocked: {path}")
            if path.endswith(("requirements.txt", "pyproject.toml", "setup.py")):
                reasons.append(f"dependency file blocked: {path}")
        combined = "\n".join(str(item.get("content", "")) for item in files).lower()
        forbidden = (
            "subprocess.popen", "os.system(", "shell=true", "pip install", "requests.",
            "urllib.request", "socket.", "winreg", "powershell", "cmd.exe", "api_token",
            "data/secrets", "chmod(", "eval(", "exec(",
        )
        for token in forbidden:
            if token in combined:
                reasons.append(f"forbidden capability marker: {token}")
        return {"auto_apply": not reasons, "reasons": reasons or ["low-risk policy passed"]}

    def _save_record(self, record: dict) -> None:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = self.state_dir / f"upgrade_{stamp}.json"
        path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
