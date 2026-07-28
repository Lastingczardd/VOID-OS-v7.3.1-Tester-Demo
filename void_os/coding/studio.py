"""Human-governed coding and upgrade studio for VOID OS.

The model may inspect approved files and draft replacements. It cannot execute
arbitrary shell commands, silently edit files, or bypass approval. Every apply
creates a rollback snapshot and runs a fixed validation suite.
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
import zipfile
from pathlib import Path

from ..kernel.paths import BASE, DATA, PROJECTS
from ..kernel.security import confined_path, sha256_file
from ..kernel.witness import log

_ALLOWED_EXTENSIONS = {".py", ".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".html", ".css", ".js", ".ts"}
_BLOCKED_NAMES = {"api_token.txt", ".env", "id_rsa", "id_ed25519"}
_MAX_FILE_BYTES = 512_000
_MAX_PROPOSAL_BYTES = 2_000_000


class CodingStudio:
    def __init__(self, cfg: dict, router):
        self.cfg = cfg
        self.router = router
        self.proposals_dir = DATA / "code_proposals"
        self.backups_dir = DATA / "backups"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)

    @property
    def project_root(self) -> Path:
        name = str(self.cfg.get("active_project", "default"))
        root = confined_path(PROJECTS, name, "code")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _root(self, target: str) -> Path:
        if target == "project":
            return self.project_root
        if target == "core":
            return BASE
        raise ValueError("Target must be project or core.")

    def list_files(self, target: str = "project") -> list[str]:
        root = self._root(target)
        files = []
        for path in root.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix()
            if self._allowed_path(rel, target):
                files.append(rel)
        return sorted(files)[:500]

    def read_file(self, rel: str, target: str = "project") -> str:
        root = self._root(target)
        path = self._safe_code_path(root, rel, target)
        if not path.exists():
            return ""
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise ValueError("File is too large for the coding studio.")
        return path.read_text(encoding="utf-8", errors="replace")

    def propose(self, instruction: str, target: str = "project", selected_files: list[str] | None = None) -> dict:
        instruction = str(instruction or "").strip()
        if not instruction:
            raise ValueError("Describe the change you want.")
        if target == "core" and not self.cfg.get("coding", {}).get("allow_core_upgrade_proposals", True):
            raise PermissionError("Core upgrade proposals are disabled in config.")

        selected_files = selected_files or self.list_files(target)[:12]
        context_parts = []
        for rel in selected_files[:20]:
            try:
                body = self.read_file(rel, target)
            except Exception:
                continue
            context_parts.append(f"FILE: {rel}\n```\n{body[:24000]}\n```")
        context = "\n\n".join(context_parts)

        prompt = f"""You are the VOID OS coding agent. Draft a safe, minimal code change.
TARGET: {target}
REQUEST: {instruction}

Return ONLY valid JSON using this schema:
{{
  "summary": "plain-language summary",
  "risk": "low|medium|high",
  "files": [{{"path": "relative/path.py", "content": "complete replacement file content"}}],
  "notes": ["important assumption"]
}}
Rules:
- Output complete replacement contents, not diffs.
- Modify only files necessary for the request.
- Never include secrets, tokens, credentials, network persistence, privilege escalation, or destructive behavior.
- Do not create installers, autoruns, hidden processes, downloaders, or arbitrary command execution.
- Keep paths relative and use only common source/document extensions.
- For core upgrades, preserve human approval, backups, path confinement, local-only defaults, and audit logs.

CURRENT FILES:
{context or '(No files yet. You may create new project files.)'}
"""
        raw = self.router.generate(
            prompt,
            "coding",
            temperature=0.2,
            max_tokens=int(self.cfg.get("coding", {}).get("proposal_output_tokens", 2400)),
            context_tokens=int(self.cfg.get("coding", {}).get("context_tokens", 12288)),
        )
        proposal = self._parse_json(raw)
        proposal["id"] = f"code-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        proposal["target"] = target
        proposal["instruction"] = instruction
        proposal["created_at"] = int(time.time())
        proposal["status"] = "proposed"
        self._validate_proposal(proposal)
        self._store_proposal(proposal)
        log("code_proposal_created", {"id": proposal["id"], "target": target, "files": [f["path"] for f in proposal["files"]]})
        return proposal

    def apply(self, proposal_id: str) -> dict:
        proposal = self.load_proposal(proposal_id)
        if proposal.get("status") == "applied":
            raise ValueError("Proposal is already applied.")
        self._validate_proposal(proposal)
        target = proposal["target"]
        root = self._root(target)
        backup = self._create_backup(proposal, root)

        written = []
        try:
            for item in proposal["files"]:
                path = self._safe_code_path(root, item["path"], target)
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, temp_name = tempfile.mkstemp(prefix=".void-write-", dir=str(path.parent))
                try:
                    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                        handle.write(item["content"])
                    os.replace(temp_name, path)
                finally:
                    if os.path.exists(temp_name):
                        os.unlink(temp_name)
                written.append(item["path"])

            checks = self.validate(target)
            if not checks["passed"]:
                self.rollback(backup)
                raise RuntimeError("Validation failed; changes were rolled back.\n" + checks["output"])

            if target == "core":
                self._refresh_manifest()
            proposal["status"] = "applied"
            proposal["applied_at"] = int(time.time())
            proposal["backup"] = str(backup)
            self._store_proposal(proposal)
            log("code_proposal_applied", {"id": proposal_id, "target": target, "files": written, "backup": str(backup)})
            return {"proposal": proposal, "checks": checks, "backup": str(backup), "restart_required": target == "core"}
        except Exception:
            log("code_proposal_apply_failed", {"id": proposal_id, "target": target})
            raise

    def validate(self, target: str = "project") -> dict:
        root = self._root(target)
        commands = [
            [sys.executable, "-m", "compileall", "-q", str(root)],
        ]
        tests = root / "tests"
        if tests.is_dir():
            commands.append([sys.executable, "-m", "unittest", "discover", "-s", str(tests), "-p", "test*.py"])
        output = []
        passed = True
        for cmd in commands:
            try:
                proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=90, shell=False)
                output.append(f"$ {' '.join(cmd)}\n{proc.stdout}{proc.stderr}".strip())
                if proc.returncode != 0:
                    passed = False
                    break
            except Exception as exc:
                output.append(f"Validation error: {exc}")
                passed = False
                break
        return {"passed": passed, "output": "\n\n".join(output) or "No checks ran."}

    def load_proposal(self, proposal_id: str) -> dict:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "", str(proposal_id))
        path = confined_path(self.proposals_dir, safe, "proposal.json")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_proposals(self) -> list[dict]:
        result = []
        for path in sorted(self.proposals_dir.glob("*/proposal.json"), reverse=True):
            try:
                result.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass
        return result[:100]

    def rollback(self, backup_path: str | Path) -> None:
        backup = Path(backup_path).resolve()
        if self.backups_dir.resolve() not in backup.parents:
            raise PermissionError("Backup is outside the backup directory.")
        metadata = json.loads((backup / "metadata.json").read_text(encoding="utf-8"))
        root = self._root(metadata["target"])
        for item in metadata["files"]:
            dst = self._safe_code_path(root, item["path"], metadata["target"])
            src = backup / "files" / item["path"]
            if item["existed"]:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            elif dst.exists():
                dst.unlink()
        if metadata["target"] == "core":
            self._refresh_manifest()
        log("code_rollback", {"backup": str(backup), "target": metadata["target"]})

    def _create_backup(self, proposal: dict, root: Path) -> Path:
        backup = self.backups_dir / f"{proposal['id']}-backup"
        (backup / "files").mkdir(parents=True, exist_ok=False)
        entries = []
        for item in proposal["files"]:
            src = self._safe_code_path(root, item["path"], proposal["target"])
            entry = {"path": item["path"], "existed": src.exists()}
            if src.exists():
                dst = backup / "files" / item["path"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                entry["sha256"] = sha256_file(src)
            entries.append(entry)
        (backup / "metadata.json").write_text(json.dumps({"proposal_id": proposal["id"], "target": proposal["target"], "files": entries}, indent=2), encoding="utf-8")
        return backup

    def _safe_code_path(self, root: Path, rel: str, target: str) -> Path:
        rel = str(rel or "").replace("\\", "/").strip("/")
        if not self._allowed_path(rel, target):
            raise PermissionError(f"Blocked code path: {rel}")
        return confined_path(root, *rel.split("/"))

    def _allowed_path(self, rel: str, target: str) -> bool:
        path = Path(rel)
        if not rel or path.is_absolute() or ".." in path.parts:
            return False
        if path.name.lower() in _BLOCKED_NAMES or path.suffix.lower() not in _ALLOWED_EXTENSIONS:
            return False
        lowered = rel.lower().replace("\\", "/")
        blocked_fragments = ("data/secrets/", ".git/", "__pycache__/", "data/logs/")
        if any(x in lowered for x in blocked_fragments):
            return False
        if target == "core":
            allowed_roots = ("void_os/", "tests/", "docs/", "config.json", "readme.md")
            if not any(lowered == x or lowered.startswith(x) for x in allowed_roots):
                return False
        return True

    def _validate_proposal(self, proposal: dict) -> None:
        files = proposal.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("Proposal contains no files.")
        total = 0
        seen = set()
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("content"), str):
                raise ValueError("Each proposed file needs path and content.")
            rel = str(item.get("path", ""))
            self._safe_code_path(self._root(proposal["target"]), rel, proposal["target"])
            if rel in seen:
                raise ValueError(f"Duplicate proposed path: {rel}")
            seen.add(rel)
            size = len(item["content"].encode("utf-8"))
            if size > _MAX_FILE_BYTES:
                raise ValueError(f"Proposed file is too large: {rel}")
            total += size
        if total > _MAX_PROPOSAL_BYTES:
            raise ValueError("Proposal exceeds the total size limit.")

    def _store_proposal(self, proposal: dict) -> None:
        folder = confined_path(self.proposals_dir, proposal["id"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "proposal.json").write_text(json.dumps(proposal, indent=2), encoding="utf-8")

    @staticmethod
    def _parse_json(raw: str) -> dict:
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start:end + 1])
            raise ValueError("Coding model did not return valid JSON.")

    def _refresh_manifest(self) -> None:
        manifest_path = BASE / "CORE_MANIFEST_SHA256.json"
        try:
            current = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
        for rel in list(current):
            path = BASE / rel
            if path.is_file():
                current[rel] = sha256_file(path)
        # Include the coding studio and UI so later tampering is detected.
        for rel in ("void_os/coding/studio.py", "void_os/ui/app.py", "config.json"):
            path = BASE / rel
            if path.is_file():
                current[rel] = sha256_file(path)
        manifest_path.write_text(json.dumps(dict(sorted(current.items())), indent=2), encoding="utf-8")
