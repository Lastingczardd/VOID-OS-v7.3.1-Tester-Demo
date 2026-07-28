"""Versioned project promotion for autonomous engineering cycles.

Each completed reasoning cycle receives its own writable project generation.
The previous generation remains untouched as a rollback/audit snapshot, while
``cfg['active_project']`` advances to the newly-created generation before code
implementation begins.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from ..kernel.security import confined_path

_RUNTIME_DIRS = {"outputs", "backups", "__pycache__", ".git"}
_PERSISTENT_AUTOPILOT_FILES = {
    "opportunities.json",
    "experiments.jsonl",
    "evidence_ledger.jsonl",
    "living_backlog.json",
    "lessons.jsonl",
}

_CYCLE_RE = re.compile(r"^(?P<base>.+?)__cycle_(?P<generation>\d+)$")


@dataclass(frozen=True)
class CyclePromotion:
    parent_project: str
    active_project: str
    root_project: str
    generation: int
    cycle: int
    created_at: int
    path: str


class CycleProjectManager:
    """Create immutable project generations and advance the active pointer."""

    def __init__(self, cfg: dict, projects_root: Path):
        self.cfg = cfg
        self.projects_root = Path(projects_root).resolve()
        self.projects_root.mkdir(parents=True, exist_ok=True)

    def promote(self, current_dir: Path, cycle: int) -> CyclePromotion:
        current_dir = Path(current_dir).resolve()
        current_name = str(self.cfg.get("active_project", current_dir.name) or current_dir.name)
        root_name, parent_generation = self._lineage(current_dir, current_name)
        generation = max(parent_generation + 1, self._next_available_generation(root_name))
        new_name = f"{root_name}__cycle_{generation:04d}"
        destination = confined_path(self.projects_root, new_name)

        while destination.exists():
            generation += 1
            new_name = f"{root_name}__cycle_{generation:04d}"
            destination = confined_path(self.projects_root, new_name)

        destination.mkdir(parents=True, exist_ok=False)
        self._copy_project(current_dir, destination)

        promotion = CyclePromotion(
            parent_project=current_name,
            active_project=new_name,
            root_project=root_name,
            generation=generation,
            cycle=int(cycle),
            created_at=int(time.time()),
            path=str(destination),
        )
        (destination / "cycle_lineage.json").write_text(
            json.dumps(asdict(promotion), indent=2), encoding="utf-8"
        )
        (destination / "autopilot").mkdir(parents=True, exist_ok=True)
        (destination / "outputs").mkdir(parents=True, exist_ok=True)
        self.cfg["active_project"] = new_name
        return promotion

    def _copy_project(self, source: Path, destination: Path) -> None:
        if not source.exists():
            (destination / "code").mkdir(parents=True, exist_ok=True)
            return
        for item in source.iterdir():
            if item.name in _RUNTIME_DIRS or item.name == "cycle_lineage.json":
                continue
            target = destination / item.name
            if item.is_symlink():
                continue
            if item.name == "autopilot" and item.is_dir():
                # Carry forward long-term learning while leaving transient
                # checkpoints and stop markers behind.  Without this, every
                # promoted generation forgot all prior attempts and repeated
                # the same work forever.
                target.mkdir(parents=True, exist_ok=True)
                for child in item.iterdir():
                    if child.is_file() and child.name in _PERSISTENT_AUTOPILOT_FILES:
                        shutil.copy2(child, target / child.name)
                continue
            if item.is_dir():
                shutil.copytree(
                    item,
                    target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".git"),
                )
            elif item.is_file():
                shutil.copy2(item, target)
        (destination / "code").mkdir(parents=True, exist_ok=True)

    def _lineage(self, current_dir: Path, current_name: str) -> tuple[str, int]:
        metadata = current_dir / "cycle_lineage.json"
        try:
            data = json.loads(metadata.read_text(encoding="utf-8"))
            root = self._safe_name(str(data.get("root_project") or current_name))
            generation = int(data.get("generation", 0))
            return root, max(0, generation)
        except Exception:
            match = _CYCLE_RE.match(current_name)
            if match:
                return self._safe_name(match.group("base")), int(match.group("generation"))
            return self._safe_name(current_name), 0

    def _next_available_generation(self, root_name: str) -> int:
        highest = 0
        prefix = f"{root_name}__cycle_"
        for child in self.projects_root.iterdir():
            if not child.is_dir() or not child.name.startswith(prefix):
                continue
            suffix = child.name[len(prefix):]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
        return highest + 1

    @staticmethod
    def _safe_name(value: str) -> str:
        value = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
        return value[:80] or "default"
