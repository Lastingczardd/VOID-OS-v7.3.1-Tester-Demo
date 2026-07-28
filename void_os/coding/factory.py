"""Bounded multi-agent software factory for VOID OS.

The factory plans, architects, codes, reviews, tests, and repairs generated
agent bundles. It never executes model-supplied commands and never installs a
bundle without explicit human approval.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable

from ..kernel.paths import BASE, DATA, PROJECTS
from ..kernel.security import confined_path
from ..kernel.witness import log

_ALLOWED_EXTENSIONS = {".py", ".json", ".md", ".txt", ".yaml", ".yml"}
_MAX_FILE_BYTES = 512_000
_MAX_FILES = 24


class AgentFactory:
    """Generate installable agents through a fixed, auditable pipeline."""

    STAGES = ("Planner", "Architect", "Coder", "Reviewer", "Tester", "Repair", "Final Approval")

    def __init__(self, cfg: dict, router):
        self.cfg = cfg
        self.router = router
        self.runs_dir = DATA / "agent_factory_runs"
        self.backups_dir = DATA / "backups" / "agent_factory"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)

    @property
    def active_project(self) -> str:
        return str(self.cfg.get("active_project", "default"))

    def build(self, spec: dict, progress: Callable[[str, dict], None] | None = None) -> dict:
        spec = self._normalize_spec(spec)
        run_id = f"factory-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        run_dir = confined_path(self.runs_dir, run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        state = {
            "id": run_id,
            "status": "running",
            "created_at": int(time.time()),
            "spec": spec,
            "stages": {},
            "repair_cycles": 0,
        }
        self._store(state)

        def emit(stage: str, status: str, detail: str = ""):
            state["stages"][stage] = {"status": status, "detail": detail, "at": int(time.time())}
            self._store(state)
            if progress:
                progress(stage, dict(state["stages"][stage]))

        try:
            emit("Planner", "running")
            plan = self._json_call(self._planner_prompt(spec), tokens=1200)
            state["plan"] = plan
            emit("Planner", "passed", plan.get("summary", "Plan created"))

            emit("Architect", "running")
            architecture = self._json_call(self._architect_prompt(spec, plan), tokens=1500)
            state["architecture"] = architecture
            emit("Architect", "passed", architecture.get("summary", "Architecture created"))

            emit("Coder", "running")
            bundle = self._json_call(self._coder_prompt(spec, plan, architecture), tokens=4200)
            files = self._validate_bundle(bundle)
            state["bundle"] = {"summary": bundle.get("summary", ""), "files": files}
            emit("Coder", "passed", f"Generated {len(files)} files")

            max_repairs = int(self.cfg.get("agent_factory", {}).get("max_repair_cycles", 2))
            review = {}
            validation = {}
            for cycle in range(max_repairs + 1):
                emit("Reviewer", "running", f"Review cycle {cycle + 1}")
                review = self._json_call(self._reviewer_prompt(spec, plan, architecture, files), tokens=1600)
                state["review"] = review
                blocking = bool(review.get("blocking", False))
                emit("Reviewer", "failed" if blocking else "passed", self._review_summary(review))

                emit("Tester", "running", f"Validation cycle {cycle + 1}")
                validation = self._validate_files(files, run_dir)
                state["validation"] = validation
                emit("Tester", "passed" if validation["passed"] else "failed", validation["summary"])

                if not blocking and validation["passed"]:
                    emit("Repair", "skipped", "No repair required")
                    break
                if cycle >= max_repairs:
                    raise RuntimeError(
                        "The generated agent did not pass review and validation after the allowed repair cycles."
                    )

                emit("Repair", "running", f"Repair cycle {cycle + 1}")
                repaired = self._json_call(
                    self._repair_prompt(spec, plan, architecture, files, review, validation),
                    tokens=4200,
                )
                files = self._validate_bundle(repaired)
                state["bundle"] = {"summary": repaired.get("summary", ""), "files": files}
                state["repair_cycles"] = cycle + 1
                emit("Repair", "passed", f"Repaired {len(files)} files")

            emit("Final Approval", "waiting", "Human review required before installation")
            state["status"] = "ready_for_approval"
            state["completed_at"] = int(time.time())
            state["bundle"]["files"] = files
            self._store(state)
            log("agent_factory_ready", {"id": run_id, "agent": spec["name"], "files": [f["path"] for f in files]})
            return state
        except Exception as exc:
            state["status"] = "failed"
            state["error"] = str(exc)
            state["failed_at"] = int(time.time())
            self._store(state)
            log("agent_factory_failed", {"id": run_id, "error": str(exc)})
            raise

    def apply(self, run_id: str) -> dict:
        state = self.load(run_id)
        if state.get("status") != "ready_for_approval":
            raise ValueError("Factory run is not ready for approval.")
        spec = state["spec"]
        files = self._validate_bundle(state["bundle"])
        slug = self._slug(spec["name"])
        project_root = confined_path(PROJECTS, self.active_project, "code", "agents", slug)
        agent_card = confined_path(DATA / "agents", f"generated_{slug}.json")
        backup = self.backups_dir / f"{run_id}-backup"
        backup.mkdir(parents=True, exist_ok=False)

        metadata = {"project_existed": project_root.exists(), "agent_card_existed": agent_card.exists()}
        if project_root.exists():
            shutil.copytree(project_root, backup / "project_bundle")
        if agent_card.exists():
            shutil.copy2(agent_card, backup / "agent_card.json")
        (backup / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        temp_parent = project_root.parent
        temp_parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix=f".{slug}-", dir=str(temp_parent)))
        try:
            for item in files:
                dst = self._safe_bundle_path(temp_dir, item["path"])
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(item["content"], encoding="utf-8", newline="\n")
            checks = self._validate_directory(temp_dir)
            if not checks["passed"]:
                raise RuntimeError("Final validation failed before install:\n" + checks["output"])

            if project_root.exists():
                shutil.rmtree(project_root)
            os.replace(temp_dir, project_root)

            card = self._extract_agent_card(files, spec, slug)
            agent_card.write_text(json.dumps(card, indent=2), encoding="utf-8")
            state["status"] = "installed"
            state["installed_at"] = int(time.time())
            state["installed_path"] = str(project_root)
            state["agent_card"] = str(agent_card)
            state["backup"] = str(backup)
            self._store(state)
            log("agent_factory_installed", {"id": run_id, "agent": spec["name"], "path": str(project_root)})
            return {"run": state, "checks": checks, "restart_required": False}
        except Exception:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            self._restore_backup(project_root, agent_card, backup, metadata)
            raise

    def load(self, run_id: str) -> dict:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "", str(run_id))
        path = confined_path(self.runs_dir, safe, "run.json")
        return json.loads(path.read_text(encoding="utf-8"))

    def _store(self, state: dict) -> None:
        run_dir = confined_path(self.runs_dir, state["id"])
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _normalize_spec(self, spec: dict) -> dict:
        name = str(spec.get("name", "")).strip()
        purpose = str(spec.get("purpose", "")).strip()
        if not name or not purpose:
            raise ValueError("Agent name and purpose are required.")
        tools = [str(x).strip() for x in spec.get("tools", []) if str(x).strip()]
        permissions = [str(x).strip() for x in spec.get("permissions", []) if str(x).strip()]
        return {
            "name": name[:80],
            "purpose": purpose[:4000],
            "tools": tools[:20],
            "permissions": permissions[:20],
            "memory": str(spec.get("memory", "project"))[:40],
            "voice": str(spec.get("voice", "professional"))[:80],
            "deployment": str(spec.get("deployment", "local"))[:80],
            "requirements": str(spec.get("requirements", ""))[:8000],
        }

    def _json_call(self, prompt: str, tokens: int) -> dict:
        raw = self.router.generate(
            prompt,
            "coding",
            temperature=0.15,
            max_tokens=tokens,
            context_tokens=int(self.cfg.get("agent_factory", {}).get("context_tokens", 16384)),
        )
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                raise ValueError("Coding model did not return valid JSON.")
            result = json.loads(match.group(0))
        if not isinstance(result, dict):
            raise ValueError("Coding model response must be a JSON object.")
        return result

    def _validate_bundle(self, bundle: dict) -> list[dict]:
        files = bundle.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("Generated bundle contains no files.")
        if len(files) > _MAX_FILES:
            raise ValueError(f"Generated bundle exceeds {_MAX_FILES} files.")
        clean = []
        seen = set()
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("Each generated file must be an object.")
            rel = str(item.get("path", "")).replace("\\", "/").strip("/")
            content = str(item.get("content", ""))
            if not rel or rel in seen:
                raise ValueError("Generated file paths must be unique and non-empty.")
            if Path(rel).suffix.lower() not in _ALLOWED_EXTENSIONS:
                raise ValueError(f"Blocked generated file extension: {rel}")
            if any(part in {"..", ".git", "secrets", "__pycache__"} for part in Path(rel).parts):
                raise ValueError(f"Unsafe generated path: {rel}")
            if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
                raise ValueError(f"Generated file is too large: {rel}")
            seen.add(rel)
            clean.append({"path": rel, "content": content})
        required = {"agent.json", "prompt.md", "README.md"}
        if not required.issubset(seen):
            raise ValueError("Agent bundle must include agent.json, prompt.md, and README.md.")
        return clean

    def _validate_files(self, files: list[dict], run_dir: Path) -> dict:
        sandbox = run_dir / "validation"
        if sandbox.exists():
            shutil.rmtree(sandbox)
        sandbox.mkdir(parents=True)
        for item in files:
            dst = self._safe_bundle_path(sandbox, item["path"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(item["content"], encoding="utf-8")
        return self._validate_directory(sandbox)

    def _validate_directory(self, root: Path) -> dict:
        outputs = []
        commands = [[sys.executable, "-m", "compileall", "-q", str(root)]]
        tests = root / "tests"
        if tests.is_dir():
            commands.append([sys.executable, "-m", "unittest", "discover", "-s", str(tests), "-p", "test*.py"])
        passed = True
        for cmd in commands:
            proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=90, shell=False)
            outputs.append(f"$ {' '.join(cmd)}\n{proc.stdout}{proc.stderr}".strip())
            if proc.returncode != 0:
                passed = False
                break
        try:
            card = json.loads((root / "agent.json").read_text(encoding="utf-8"))
            if not isinstance(card, dict) or not card.get("name") or not card.get("role"):
                raise ValueError("agent.json requires name and role.")
        except Exception as exc:
            passed = False
            outputs.append(f"Agent card validation: {exc}")
        summary = "All fixed checks passed" if passed else "One or more fixed checks failed"
        return {"passed": passed, "summary": summary, "output": "\n\n".join(outputs)}

    def _safe_bundle_path(self, root: Path, rel: str) -> Path:
        candidate = (root / rel).resolve()
        if candidate != root.resolve() and root.resolve() not in candidate.parents:
            raise PermissionError("Generated path escapes the bundle root.")
        return candidate

    def _extract_agent_card(self, files: list[dict], spec: dict, slug: str) -> dict:
        raw = next(item["content"] for item in files if item["path"] == "agent.json")
        card = json.loads(raw)
        return {
            "name": str(card.get("name") or spec["name"]),
            "role": str(card.get("role") or spec["purpose"]),
            "goal": str(card.get("goal") or spec["purpose"]),
            "voice": str(card.get("voice") or spec["voice"]),
            "permissions": list(card.get("permissions") or spec["permissions"]),
            "tools": list(card.get("tools") or spec["tools"]),
            "temperature": float(card.get("temperature", 0.35)),
            "generated_bundle": f"projects/{self.active_project}/code/agents/{slug}",
        }

    def _restore_backup(self, project_root: Path, agent_card: Path, backup: Path, metadata: dict) -> None:
        if project_root.exists():
            shutil.rmtree(project_root, ignore_errors=True)
        if metadata.get("project_existed") and (backup / "project_bundle").exists():
            shutil.copytree(backup / "project_bundle", project_root)
        if agent_card.exists():
            agent_card.unlink()
        if metadata.get("agent_card_existed") and (backup / "agent_card.json").exists():
            shutil.copy2(backup / "agent_card.json", agent_card)

    @staticmethod
    def _slug(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        return slug[:60] or "generated_agent"

    @staticmethod
    def _review_summary(review: dict) -> str:
        issues = review.get("issues", [])
        return str(review.get("summary") or f"{len(issues) if isinstance(issues, list) else 0} issues")

    def _planner_prompt(self, spec: dict) -> str:
        return f"""You are the Planner in a bounded software engineering team.
Create a practical build plan for this local VOID OS agent.
SPECIFICATION:\n{json.dumps(spec, indent=2)}
Return ONLY JSON: {{"summary":"...","tasks":["..."],"acceptance_criteria":["..."],"risks":["..."]}}.
Do not propose network persistence, credential collection, surveillance, destructive behavior, or arbitrary shell execution.
"""

    def _architect_prompt(self, spec: dict, plan: dict) -> str:
        return f"""You are the Architect. Design a small installable Python agent bundle for VOID OS.
SPEC:\n{json.dumps(spec, indent=2)}
PLAN:\n{json.dumps(plan, indent=2)}
The bundle must include agent.json, prompt.md, README.md, tools.py, and tests/test_agent.py.
Tools must be pure local functions unless explicitly named in the spec. No subprocess, eval, exec, dynamic imports, downloaders, autoruns, hidden processes, or secret access.
Return ONLY JSON: {{"summary":"...","files":[{{"path":"...","purpose":"..."}}],"interfaces":["..."],"test_plan":["..."]}}.
"""

    def _coder_prompt(self, spec: dict, plan: dict, architecture: dict) -> str:
        return f"""You are the Coder. Produce the complete agent bundle described below.
SPEC:\n{json.dumps(spec, indent=2)}
PLAN:\n{json.dumps(plan, indent=2)}
ARCHITECTURE:\n{json.dumps(architecture, indent=2)}
Return ONLY JSON using {{"summary":"...","files":[{{"path":"relative/path","content":"complete file content"}}]}}.
Required files: agent.json, prompt.md, README.md, tools.py, tests/test_agent.py.
Use Python standard library only. tests must run with unittest. Keep tools deterministic and local. Never use subprocess, os.system, eval, exec, sockets, arbitrary file deletion, hidden persistence, credential access, or remote downloads. The agent card requires name, role, goal, permissions, tools, voice, and temperature.
"""

    def _reviewer_prompt(self, spec: dict, plan: dict, architecture: dict, files: list[dict]) -> str:
        compact = [{"path": f["path"], "content": f["content"][:16000]} for f in files]
        return f"""You are the Reviewer. Review this generated agent for correctness, completeness, maintainability, and security.
SPEC:\n{json.dumps(spec, indent=2)}
PLAN:\n{json.dumps(plan, indent=2)}
ARCHITECTURE:\n{json.dumps(architecture, indent=2)}
FILES:\n{json.dumps(compact, indent=2)}
Return ONLY JSON: {{"summary":"...","blocking":false,"issues":[{{"severity":"low|medium|high","file":"...","problem":"...","fix":"..."}}],"checks":["..."]}}.
Mark blocking true for syntax errors, missing requirements, unsafe capabilities, tests that do not test behavior, or an invalid agent card.
"""

    def _repair_prompt(self, spec: dict, plan: dict, architecture: dict, files: list[dict], review: dict, validation: dict) -> str:
        return f"""You are the Repair Agent. Return a complete corrected replacement bundle.
SPEC:\n{json.dumps(spec, indent=2)}
PLAN:\n{json.dumps(plan, indent=2)}
ARCHITECTURE:\n{json.dumps(architecture, indent=2)}
REVIEW:\n{json.dumps(review, indent=2)}
VALIDATION:\n{json.dumps(validation, indent=2)}
CURRENT FILES:\n{json.dumps(files, indent=2)}
Return ONLY JSON using {{"summary":"...","files":[{{"path":"relative/path","content":"complete file content"}}]}}.
Preserve required files and fix every blocking issue. Standard library only. Never add subprocess, eval, exec, sockets, downloads, persistence, destructive behavior, or secret access.
"""
