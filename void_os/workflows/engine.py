"""Runs a saved workflow: a linear list of nodes (input / agent / team /
save / plugin / approval) piping one value from step to step.

Compared to the original engine, a failure inside a node no longer
throws away every output that ran before it -- run() always returns a
result dict with whatever completed, plus an 'error' field if something
went wrong.
"""

import json
import re
import time
from ..kernel.paths import PROJECTS, DATA
from ..kernel.witness import log
from ..kernel.security import safe_segment, confined_path

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\- ]{1,80}$")
VALID_NODE_TYPES = {"input", "agent", "team", "save", "plugin", "approval"}


class WorkflowEngine:
    def __init__(self, cfg, agents, plugins):
        self.cfg = cfg
        self.agents = agents
        self.plugins = plugins

    def _workflow_dir(self):
        project = safe_segment(self.cfg.get("active_project", "default"), "project name")
        return confined_path(PROJECTS, project, "workflows")

    def _export_dir(self):
        project = safe_segment(self.cfg.get("active_project", "default"), "project name")
        return confined_path(PROJECTS, project, "exports")

    def path(self, name: str):
        if not _SAFE_NAME.match(name):
            raise ValueError(f"Invalid workflow name: {name!r}")
        return self._workflow_dir() / f"{name}.json"

    def list(self) -> list:
        d = self._workflow_dir()
        d.mkdir(parents=True, exist_ok=True)
        return sorted(p.stem for p in d.glob("*.json"))

    def save(self, name: str, nodes: list) -> None:
        for n in nodes:
            if n.get("type") not in VALID_NODE_TYPES:
                raise ValueError(f"Unknown node type: {n.get('type')!r}")
        self.path(name).parent.mkdir(parents=True, exist_ok=True)
        self.path(name).write_text(json.dumps({"name": name, "nodes": nodes}, indent=2), encoding="utf-8")

    def load(self, name: str) -> dict:
        return json.loads(self.path(name).read_text(encoding="utf-8"))

    def delete(self, name: str) -> None:
        p = self.path(name)
        if p.exists():
            p.unlink()

    def run(self, name: str, input_text: str = "") -> dict:
        wf = self.load(name)
        input_text = str(input_text or "")
        max_input = int(self.cfg.get("security", {}).get("max_workflow_input_chars", 50000))
        if len(input_text) > max_input:
            raise ValueError(f"Workflow input exceeds security limit of {max_input} characters.")
        value = input_text
        outputs = []
        start = time.time()
        max_steps = self.cfg.get("budgets", {}).get("max_workflow_steps", 25)
        max_runtime = self.cfg.get("budgets", {}).get("max_runtime_seconds", 900)
        error = None
        saved_files = []

        for i, node in enumerate(wf.get("nodes", [])[:max_steps]):
            if time.time() - start > max_runtime:
                error = "Workflow runtime budget exceeded."
                break
            kind = node.get("type")
            try:
                if kind == "input":
                    # Runtime input is authoritative. The node text is a fallback,
                    # not a value that silently overwrites what the user entered.
                    value = value.strip() or str(node.get("text", "")).strip()
                    if not value:
                        raise ValueError("Input is empty. Enter customer details before running.")
                elif kind == "agent":
                    value = self.agents.run(node.get("agent"), node.get("prompt", "") + "\nINPUT:\n" + value)
                elif kind == "team":
                    value = self.agents.team_run(node.get("prompt", "") + "\nINPUT:\n" + value)["synthesis"]
                elif kind == "save":
                    filename = Path(str(node.get("filename", "workflow_output.txt"))).name
                    if filename in {"", ".", ".."}: raise ValueError("Invalid export filename.")
                    target = confined_path(self._export_dir(), filename)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(str(value), encoding="utf-8")
                    saved_files.append(str(target))
                    # Preserve the generated content for downstream nodes and UI.
                elif kind == "plugin":
                    value = str(self.plugins.invoke(node["plugin"], node["tool"], node.get("args", {})))
                elif kind == "approval":
                    queue = DATA / "approval_queue.jsonl"
                    with queue.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"workflow": name, "request": value, "node": node}) + "\n")
                    value = "Approval requested and queued. Workflow stopped."
                    outputs.append({"node": i, "type": kind, "output": value})
                    break
                else:
                    raise ValueError(f"Unknown node type: {kind!r}")
            except Exception as e:
                error = f"Step {i} ({kind}) failed: {e}"
                outputs.append({"node": i, "type": kind, "output": None, "error": str(e)})
                break
            outputs.append({"node": i, "type": kind, "output": value})

        log("workflow_run", {"workflow": name, "steps": len(outputs), "error": error})
        return {
            "outputs": outputs,
            "error": error,
            "final_value": value,
            "saved_files": saved_files,
        }
