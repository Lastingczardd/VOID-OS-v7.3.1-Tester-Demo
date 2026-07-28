"""Central filesystem layout for VOID OS.

All other modules import their directories from here so the on-disk
layout only has to be defined once.
"""

from pathlib import Path

# Repo root is three levels up from this file (void_os/kernel/paths.py).
BASE = Path(__file__).resolve().parents[2]

DATA = BASE / "data"
PROJECTS = BASE / "projects"
PLUGINS = BASE / "plugins"
KNOWLEDGE = BASE / "knowledge"
DATASETS = BASE / "datasets"

# Directories that must exist before the app touches them.
_REQUIRED_DIRS = [
    DATA,
    DATA / "agents",
    DATA / "logs",
    DATA / "memory",
    DATA / "checkpoints",
    DATA / "secrets",
    PROJECTS,
    PROJECTS / "default" / "workflows",
    PROJECTS / "default" / "exports",
    PLUGINS,
]

for _p in _REQUIRED_DIRS:
    _p.mkdir(parents=True, exist_ok=True)
