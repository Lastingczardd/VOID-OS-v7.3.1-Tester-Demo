"""Tkinter front end for VOID OS Forge.

Four tabs: Chat, Workflow Canvas, Agents, System. This module only
handles presentation and user interaction -- all real work (talking to
Ollama, running agents, running workflows) is delegated to
void_os.models / void_os.agents / void_os.workflows.
"""

import json
import os
import queue
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from ..kernel.config import load, save
from ..kernel.paths import BASE, PROJECTS
from ..models.router import ModelRouter
from ..agents.runtime import AgentRuntime
from ..plugins.manager import PluginManager
from ..workflows.engine import WorkflowEngine
from ..api.server import start_api
from ..coding.studio import CodingStudio
from ..coding.factory import AgentFactory
from ..autopilot.self_upgrade import SelfUpgradeController
from ..autopilot.implementation import AutonomousImplementationController
from ..autopilot.governor import AutonomyGovernor
from ..autopilot.engineering_kernel import AutonomousEngineeringKernel
from ..autopilot.cycle_projects import CycleProjectManager
from ..media.image_generator import AutonomousImageGenerator
from ..research.engine import AutonomousResearchEngine

# --- Theme: emerald / gold / magenta on black, matching the rest of VOID OS ---
BG = "#0a0a0c"
PANEL = "#131318"
TEXT = "#d9f7e8"
EMERALD = "#12d98a"
GOLD = "#d4af37"
MAGENTA = "#ff3fc0"
MUTED = "#6c7a76"
NODE_COLORS = {
    "input": EMERALD,
    "agent": GOLD,
    "team": GOLD,
    "save": MUTED,
    "plugin": MAGENTA,
    "approval": MAGENTA,
}


class ForgeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VOID OS v7.3.1 TESTER DEMO")
        self.geometry("1280x820")
        self.configure(bg=BG)

        self.cfg = load()
        self.router = ModelRouter(self.cfg)
        self.agents = AgentRuntime(self.router)
        self.plugins = PluginManager(self.cfg)
        self.workflows = WorkflowEngine(self.cfg, self.agents, self.plugins)
        self.coding = CodingStudio(self.cfg, self.router)
        self.agent_factory = AgentFactory(self.cfg, self.router)
        self.self_upgrade = SelfUpgradeController(self.cfg, self.coding)
        self.implementation = AutonomousImplementationController(self.cfg, self.coding)
        self.image_generator = AutonomousImageGenerator(self.cfg, self.router)
        self.research_engine = AutonomousResearchEngine(self.cfg, self.router)
        self.governor = None
        self.engineering_kernel = None
        self.cycle_projects = CycleProjectManager(self.cfg, PROJECTS)
        self.api = None

        self.current_workflow = None
        self.nodes = []          # list of node dicts for the loaded workflow
        self.node_positions = {} # node index -> (x, y) on the canvas
        self.drag_index = None
        self.busy = False
        self.autopilot_running = False
        self.autopilot_stop = threading.Event()
        self.autopilot_thread = None
        self._ui_queue = queue.Queue()
        self._closing = False
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._apply_theme()
        self._build()
        self._ensure_sample_workflow()
        self.refresh_all()
        self._check_connection()
        self.after(50, self._drain_ui_queue)

        if self.cfg.get("api", {}).get("enabled"):
            self.start_api_now()

    # ---------------------------------------------------------------- theme
    def _apply_theme(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=TEXT, padding=(14, 6))
        style.map("TNotebook.Tab", background=[("selected", EMERALD)], foreground=[("selected", "#03130a")])
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("TButton", background=PANEL, foreground=EMERALD, borderwidth=1)
        style.map("TButton", background=[("active", EMERALD)], foreground=[("active", "#03130a")])
        style.configure("TEntry", fieldbackground=PANEL, foreground=TEXT)

    def _text_widget(self, parent, **kw):
        return tk.Text(parent, wrap="word", bg=PANEL, fg=TEXT, insertbackground=EMERALD,
                        borderwidth=0, highlightthickness=1, highlightbackground=MUTED, **kw)

    # ---------------------------------------------------------------- build
    def _build(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        self.mission_tab = ttk.Frame(nb)
        self.chat_tab = ttk.Frame(nb)
        self.flow_tab = ttk.Frame(nb)
        self.agent_tab = ttk.Frame(nb)
        self.code_tab = ttk.Frame(nb)
        self.factory_tab = ttk.Frame(nb)
        self.system_tab = ttk.Frame(nb)
        nb.add(self.mission_tab, text="VOID OS")
        # Advanced workspaces remain available to the autonomous runtime but are
        # intentionally hidden from the human-facing interface. The operator's
        # entire job is START and STOP.

        self._build_mission_tab()
        self._build_chat_tab()
        self._build_flow_tab()
        self._build_agent_tab()
        self._build_code_tab()
        self._build_factory_tab()
        self._build_system_tab()

        self.status = tk.StringVar(value="Starting...")
        bar = tk.Frame(self, bg=PANEL)
        bar.pack(fill="x", side="bottom")
        tk.Label(bar, textvariable=self.status, bg=PANEL, fg=MUTED, anchor="w").pack(fill="x", padx=8, pady=3)


    def _build_mission_tab(self):
        outer = ttk.Frame(self.mission_tab)
        outer.pack(fill="both", expand=True, padx=24, pady=24)

        tk.Label(outer, text="VOID OS TESTER DEMO", bg=BG, fg=GOLD,
                 font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(
            outer,
            text="Press START to run a bounded autonomous engineering demonstration. Each run is capped, measured, reversible, and parked automatically.",
            bg=BG, fg=MUTED, font=("Segoe UI", 11)
        ).pack(anchor="w", pady=(2, 16))

        controls = ttk.Frame(outer)
        controls.pack(fill="x")
        self.mission_start_btn = ttk.Button(controls, text="▶ START", command=self.start_autopilot)
        self.mission_start_btn.pack(side="left", ipadx=45, ipady=18, padx=(0, 10))
        self.mission_stop_btn = ttk.Button(controls, text="■ STOP", command=self.stop_autopilot, state="disabled")
        self.mission_stop_btn.pack(side="left", ipadx=45, ipady=18)

        self.mission_state = tk.StringVar(value="IDLE")
        tk.Label(controls, textvariable=self.mission_state, bg=BG, fg=EMERALD,
                 font=("Segoe UI", 12, "bold")).pack(side="right", padx=8)

        self.mission_summary = tk.StringVar(value=self._demo_summary())
        tk.Label(outer, textvariable=self.mission_summary, bg=BG, fg=TEXT,
                 font=("Segoe UI", 11), anchor="w", justify="left").pack(fill="x", pady=(18, 8))

        tk.Label(outer, text="Autopilot log", bg=BG, fg=TEXT).pack(anchor="w", pady=(8, 4))
        self.mission_output = self._text_widget(outer, font=("Consolas", 10))
        self.mission_output.pack(fill="both", expand=True)

    def _demo_usage_path(self):
        path = BASE / "data" / "demo_usage.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _demo_limits(self):
        demo = self.cfg.get("demo", {})
        return (
            max(1, int(demo.get("max_cycles_per_run", 3))),
            max(1, int(demo.get("max_total_cycles", 10))),
        )

    def _demo_usage(self):
        path = self._demo_usage_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return max(0, int(data.get("completed_cycles", 0)))
        except Exception:
            return 0

    def _record_demo_cycle(self):
        used = self._demo_usage() + 1
        self._demo_usage_path().write_text(
            json.dumps({"completed_cycles": used, "updated_at": time.strftime("%Y%m%d_%H%M%S")}, indent=2),
            encoding="utf-8",
        )
        return used

    def _demo_summary(self):
        per_run, total = self._demo_limits()
        used = self._demo_usage()
        remaining = max(0, total - used)
        return (
            f"Tester Demo • {per_run} cycles per START • {remaining}/{total} cycles remaining • "
            "core self-upgrades disabled • local project changes remain backed up and reversible"
        )

    def start_autopilot(self):
        if self.autopilot_running:
            return
        _, total_limit = self._demo_limits()
        used = self._demo_usage()
        if self.cfg.get("demo", {}).get("enabled", False) and used >= total_limit:
            self.mission_state.set("DEMO COMPLETE")
            self.mission_summary.set(self._demo_summary())
            self._mission_log("DEMO LIMIT REACHED: This tester build has completed its licensed cycle allowance.")
            messagebox.showinfo(
                "VOID OS Tester Demo",
                "The demo cycle allowance is complete. Your outputs and evidence remain available in the projects folder."
            )
            return
        self.autopilot_running = True
        self.autopilot_stop.clear()
        self.mission_start_btn.configure(state="disabled")
        self.mission_stop_btn.configure(state="normal")
        self.mission_state.set("RUNNING")
        self.mission_output.delete("1.0", "end")
        objective = self.cfg.get("autopilot", {}).get(
            "objective",
            "Continuously improve logic, creative problem solving, reliability, and usefulness."
        )
        self._mission_log("START: " + objective)
        self.autopilot_thread = threading.Thread(
            target=self._autopilot_worker, args=(objective,), daemon=True
        )
        self.autopilot_thread.start()

    def stop_autopilot(self):
        self.autopilot_stop.set()
        self.mission_state.set("STOPPING")
        self._mission_log("STOP requested. Finishing the current safe step and saving a checkpoint...")

    def _mission_log(self, text):
        def write(value):
            if not hasattr(self, "mission_output"):
                return
            self.mission_output.insert("end", value + "\n")
            self.mission_output.see("end")
        self._ui_queue.put((write, text))

    def _autopilot_worker(self, objective):
        cycle = 0
        recent_tasks = []
        try:
            project_dir = self._mission_project_dir()
            state_dir = project_dir / "autopilot"
            output_dir = project_dir / "outputs"
            state_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)
            self.governor = AutonomyGovernor(self.cfg, self.router, state_dir)
            self.engineering_kernel = AutonomousEngineeringKernel(self.cfg, self.router, self.agents, state_dir)
            pause_seconds = max(1, int(self.cfg.get("autopilot", {}).get("cycle_pause_seconds", 3)))

            per_run_limit, total_limit = self._demo_limits()
            while not self.autopilot_stop.is_set():
                if self.cfg.get("demo", {}).get("enabled", False):
                    if cycle >= per_run_limit:
                        self._mission_log("DEMO RUN COMPLETE: per-START cycle budget reached. Parking safely.")
                        break
                    if self._demo_usage() >= total_limit:
                        self._mission_log("DEMO COMPLETE: total cycle allowance reached. Parking safely.")
                        break
                cycle += 1
                self._mission_log(f"\n=== DEMO CYCLE {cycle}/{per_run_limit} ===")
                context = self._mission_project_context(project_dir)
                packet = self.engineering_kernel.prepare_cycle(objective, context, recent_tasks, cycle)
                task = packet["task"]
                if self.autopilot_stop.is_set():
                    break
                recent_tasks = (recent_tasks + [task])[-5:]
                self._mission_log("KERNEL OPPORTUNITY: " + task)
                self._mission_log("EVIDENCE: " + packet["opportunity"].get("evidence", ""))
                self._mission_log("EXPERIMENT WINNER: " + packet["winner"].get("name", ""))
                team = packet["team"]
                self._mission_log("DYNAMIC TEAM: " + ", ".join(team))

                research_digest = ""
                if self.research_engine.should_run(cycle) and not self.autopilot_stop.is_set():
                    self._mission_log("RESEARCH FORGE: investigating the current task through approved public sources...")
                    try:
                        research = self.research_engine.research_for_cycle(
                            cycle, task, context, output_dir / "research"
                        )
                        if research.get("status") == "COMPLETE":
                            research_digest = research.get("digest", "")
                            self._mission_log(
                                f"RESEARCH COMPLETE: {len(research.get('sources', []))} sources; "
                                + research.get("brief", "")
                            )
                        else:
                            self._mission_log("RESEARCH SKIPPED: " + research.get("reason", "No usable evidence"))
                    except Exception as exc:
                        self._mission_log("RESEARCH FAILED SAFELY: " + str(exc))

                goal = (
                    f"AUTOPILOT OBJECTIVE: {objective}\n"
                    f"CURRENT TASK: {task}\n\n"
                    f"PROJECT EVIDENCE:\n{context}\n\n"
                    f"{self.engineering_kernel.experiment_brief(packet)}\n\n"
                    + (f"PUBLIC RESEARCH BRIEF:\n{research_digest}\n\n" if research_digest else "")
                    + "Implement the selected experiment approach as one concrete, immediately useful deliverable grounded only in the supplied evidence. "
                    "Use research as supporting evidence, not unquestioned truth. Check your logic, include a verification "
                    "method, and do not claim actions that were not performed."
                )
                result = self.agents.team_run(goal, names=team)
                text = result if isinstance(result, str) else result.get("synthesis", str(result))
                if self.autopilot_stop.is_set():
                    self._mission_log("Current cycle completed after STOP. Saving before shutdown.")

                # Every completed reasoning cycle becomes a new active project
                # before implementation. This preserves the parent generation,
                # gives the coding studio a populated successor workspace, and
                # ensures all applied changes become the basis of the next cycle.
                parent_project = self.cfg.get("active_project", project_dir.name)
                promotion = CycleProjectManager(self.cfg, project_dir.parent).promote(project_dir, cycle)
                save(self.cfg)
                project_dir = Path(promotion.path)
                state_dir = project_dir / "autopilot"
                output_dir = project_dir / "outputs"
                state_dir.mkdir(parents=True, exist_ok=True)
                output_dir.mkdir(parents=True, exist_ok=True)
                self._mission_log(
                    f"CYCLE PROMOTED: {parent_project} -> {promotion.active_project}"
                )
                self._mission_log("ACTIVE PROJECT: " + promotion.active_project)

                stamp = time.strftime("%Y%m%d_%H%M%S")
                out_file = output_dir / f"autopilot_cycle_{cycle:04d}_{stamp}.md"
                out_file.write_text(
                    f"# Autonomous Engineering Cycle {cycle}\n\n## Opportunity\n{task}\n\n## Experiment\n{self.engineering_kernel.experiment_brief(packet)}\n\n## Result\n{text}\n",
                    encoding="utf-8"
                )
                checkpoint = {
                    "cycle": cycle,
                    "objective": objective,
                    "last_task": task,
                    "recent_tasks": recent_tasks,
                    "last_output": str(out_file),
                    "active_project": promotion.active_project,
                    "parent_project": promotion.parent_project,
                    "generation": promotion.generation,
                    "updated_at": stamp,
                    "status": "stopping" if self.autopilot_stop.is_set() else "running",
                }
                (state_dir / "checkpoint.json").write_text(
                    json.dumps(checkpoint, indent=2), encoding="utf-8"
                )
                self._mission_log("RESULT SAVED: " + str(out_file))
                if self.governor:
                    self.governor.record_outcome(task, str(out_file), True, text)

                if self.image_generator.should_run(cycle) and not self.autopilot_stop.is_set():
                    self._mission_log("IMAGE FORGE: designing and rendering a useful project visual...")
                    visual = self.image_generator.generate_for_cycle(cycle, task, context, text, output_dir / "images")
                    if visual.get("status") == "GENERATED":
                        self._mission_log("IMAGE GENERATED: " + visual.get("image", ""))
                    elif visual.get("status") == "SKIPPED":
                        self._mission_log("IMAGE FORGE SKIPPED: " + visual.get("reason", ""))
                    else:
                        self._mission_log("IMAGE FORGE: " + visual.get("status", "UNKNOWN") + " " + visual.get("error", ""))

                implementation_result = {}
                if not self.autopilot_stop.is_set():
                    self._mission_log("IMPLEMENTATION: converting the winning experiment into a tested project upgrade...")
                    try:
                        implementation = self.implementation.run_once(task, context + ("\n\nRESEARCH BRIEF:\n" + research_digest if research_digest else ""), text)
                        implementation_result = implementation
                        status = implementation.get("status", "UNKNOWN")
                        if status == "APPLIED":
                            self._mission_log("PROJECT UPGRADE APPLIED: " + implementation.get("summary", ""))
                            self._mission_log("BACKUP: " + str(implementation.get("backup", "")))
                        elif status == "SKIPPED":
                            self._mission_log("PROJECT IMPLEMENTATION SKIPPED: " + implementation.get("reason", ""))
                        elif status == "REJECTED":
                            reasons = "; ".join(implementation.get("decision", {}).get("reasons", []))
                            self._mission_log("PROJECT UPGRADE REJECTED BY SAFETY POLICY: " + reasons)
                        else:
                            self._mission_log("PROJECT IMPLEMENTATION: " + status + " " + implementation.get("error", ""))
                    except Exception as exc:
                        self._mission_log("PROJECT IMPLEMENTATION FAILED SAFELY: " + str(exc))

                if self.self_upgrade.should_run(cycle) and not self.autopilot_stop.is_set():
                    self._mission_log("CORE EVOLUTION: preparing one bounded VOID OS upgrade...")
                    try:
                        upgrade = self.self_upgrade.run_once(task, context + ("\n\nRESEARCH BRIEF:\n" + research_digest if research_digest else "") + "\n\nLAST RESULT:\n" + text[:8000])
                        if upgrade.get("status") == "APPLIED":
                            self._mission_log("CORE UPGRADE APPLIED: " + upgrade.get("summary", ""))
                            self._mission_log("BACKUP: " + str(upgrade.get("backup", "")))
                            if upgrade.get("restart_required"):
                                self._mission_log("RESTART NOTE: upgrade is active on the next START of VOID OS.")
                        else:
                            reasons = "; ".join(upgrade.get("decision", {}).get("reasons", []))
                            self._mission_log("CORE UPGRADE REJECTED BY SAFETY POLICY: " + reasons)
                    except Exception as exc:
                        self._mission_log("CORE EVOLUTION SKIPPED: " + str(exc))

                if self.engineering_kernel:
                    self.engineering_kernel.outcome(packet, True, str(out_file), text, implementation_result)
                self._mission_log("EVIDENCE LEDGER UPDATED")
                if self.cfg.get("demo", {}).get("enabled", False):
                    used = self._record_demo_cycle()
                    _, total_limit = self._demo_limits()
                    self._mission_log(f"DEMO METER: {used}/{total_limit} total cycles used")
                    self._ui_queue.put((lambda value: self.mission_summary.set(value), self._demo_summary()))
                self._mission_log("CYCLE COMPLETE")

                # The promoted project is now the sole working head. Rebind
                # stateful cycle services so the next observation, backlog, and
                # evidence ledger continue from this newly implemented version.
                self.governor = AutonomyGovernor(self.cfg, self.router, state_dir)
                self.engineering_kernel = AutonomousEngineeringKernel(
                    self.cfg, self.router, self.agents, state_dir
                )

                if self.autopilot_stop.wait(pause_seconds):
                    break
        except Exception as exc:
            self._mission_log(f"ERROR: {exc}")
        finally:
            try:
                project_dir = self._mission_project_dir()
                state_dir = project_dir / "autopilot"
                state_dir.mkdir(parents=True, exist_ok=True)
                (state_dir / "stopped.json").write_text(
                    json.dumps({"stopped_at": time.strftime("%Y%m%d_%H%M%S"), "cycles": cycle}, indent=2),
                    encoding="utf-8"
                )
            except Exception:
                pass
            def finish(_):
                self.autopilot_running = False
                self.mission_start_btn.configure(state="normal")
                self.mission_stop_btn.configure(state="disabled")
                self.mission_state.set("IDLE")
                self._set_status("Autopilot stopped safely")
            self._ui_queue.put((finish, None))

    def _choose_autopilot_task(self, objective, context, recent_tasks):
        """Select work from observed evidence without asking the operator to plan."""
        if self.governor is None:
            project_dir = self._mission_project_dir()
            self.governor = AutonomyGovernor(self.cfg, self.router, project_dir / "autopilot")
        return self.governor.choose_task(objective, context, recent_tasks)

    def _mission_project_dir(self):
        """Return a writable absolute project directory.

        Relative paths are anchored to the application folder instead of the
        process working directory, which may be a protected Windows folder.
        """
        configured = self.cfg.get("projects_dir")
        root = Path(configured).expanduser() if configured else PROJECTS
        if not root.is_absolute():
            root = BASE / root
        project = root / self.cfg.get("active_project", "default")
        try:
            project.mkdir(parents=True, exist_ok=True)
            probe = project / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return project
        except (OSError, PermissionError):
            fallback_root = Path(
                os.environ.get("LOCALAPPDATA", str(Path.home() / ".void_os"))
            ) / "VOID_OS" / "projects"
            project = fallback_root / self.cfg.get("active_project", "default")
            project.mkdir(parents=True, exist_ok=True)
            self._mission_log(f"WRITE FALLBACK: {project}")
            return project

    def _mission_project_context(self, project_dir, max_chars=12000):
        """Build a small evidence bundle instead of asking agents to guess."""
        roots = [project_dir, BASE]
        preferred = {"readme.md", "config.json", "pyproject.toml", "requirements.txt"}
        seen = set()
        chunks = []
        total = 0
        for root in roots:
            if not root.exists():
                continue
            files = [x for x in root.rglob("*") if x.is_file()]
            files.sort(key=lambda x: (x.name.lower() not in preferred, len(x.parts), x.name.lower()))
            for path in files:
                if path in seen or any(part in {"__pycache__", ".git", "outputs", "backups"} for part in path.parts):
                    continue
                seen.add(path)
                try:
                    rel = path.relative_to(BASE) if path.is_relative_to(BASE) else path
                    if path.suffix.lower() not in {".py", ".json", ".md", ".txt", ".toml", ".yaml", ".yml"}:
                        continue
                    body = path.read_text(encoding="utf-8", errors="replace")[:1800]
                    chunk = f"\n--- FILE: {rel} ---\n{body}"
                    if total + len(chunk) > max_chars:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                except OSError:
                    continue
            if total >= max_chars:
                break
        return "".join(chunks) or "No readable project files were found."

    def _mission_team(self, goal):
        available = self.agents.agents
        lower = goal.lower()
        if any(word in lower for word in ("error", "bug", "broken", "fail", "crash")):
            wanted = ["Debugger", "Architect", "Coder", "Test Engineer", "Security Sentinel"]
        elif any(word in lower for word in ("sell", "money", "business", "customer", "offer")):
            wanted = ["Product Builder", "Business Strategist", "Architect", "Critic", "Release Manager"]
        else:
            wanted = ["Architect", "Debugger", "Product Builder", "Security Sentinel", "Release Manager"]
        selected = [name for name in wanted if name in available]
        return selected or list(available)[:5]

    def _build_chat_tab(self):
        self.chatout = self._text_widget(self.chat_tab)
        self.chatout.pack(fill="both", expand=True, padx=8, pady=8)
        bar = ttk.Frame(self.chat_tab)
        bar.pack(fill="x", padx=8, pady=8)
        self.chatq = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.chatq)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self.ask())
        self.ask_btn = ttk.Button(bar, text="Ask", command=self.ask)
        self.ask_btn.pack(side="left", padx=5)
        self.team_btn = ttk.Button(bar, text="Run Agent Team", command=self.team)
        self.team_btn.pack(side="left")

    def _build_flow_tab(self):
        left = ttk.Frame(self.flow_tab)
        left.pack(side="left", fill="y", padx=8, pady=8)
        right = ttk.Frame(self.flow_tab)
        right.pack(side="left", fill="both", expand=True, padx=8, pady=8)

        self.wflist = tk.Listbox(left, width=28, bg=PANEL, fg=TEXT, selectbackground=EMERALD,
                                  highlightthickness=0, borderwidth=0)
        self.wflist.pack(fill="y", expand=True)
        ttk.Button(left, text="New Workflow", command=self.new_workflow).pack(fill="x", pady=(6, 0))
        ttk.Button(left, text="Load", command=self.load_workflow).pack(fill="x")
        ttk.Button(left, text="Rename", command=self.rename_workflow).pack(fill="x")
        ttk.Button(left, text="Delete Workflow", command=self.delete_workflow).pack(fill="x")
        ttk.Button(left, text="Run", command=self.run_workflow).pack(fill="x", pady=(10, 0))

        self.canvas = tk.Canvas(right, bg="#0f0f12", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<Double-Button-1>", self._on_canvas_double_click)
        self.canvas.bind("<Button-3>", self._on_canvas_right_click)

        controls = ttk.Frame(right)
        controls.pack(fill="x")
        for kind in ["input", "agent", "team", "save", "plugin", "approval"]:
            ttk.Button(controls, text="+ " + kind, command=lambda k=kind: self.add_node(k)).pack(side="left")
        tk.Label(right, fg=MUTED, bg=BG,
                 text="Drag a node to move it. Double-click to edit. Right-click to delete.").pack(anchor="w")

    def _build_agent_tab(self):
        self.agentlist = tk.Listbox(self.agent_tab, bg=PANEL, fg=TEXT, selectbackground=GOLD,
                                     highlightthickness=0, borderwidth=0, width=24)
        self.agentlist.pack(side="left", fill="y", padx=8, pady=8)
        self.agentview = self._text_widget(self.agent_tab)
        self.agentview.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        self.agentlist.bind("<<ListboxSelect>>", self.show_agent)


    def _build_code_tab(self):
        top = ttk.Frame(self.code_tab)
        top.pack(fill="x", padx=8, pady=8)
        tk.Label(top, text="Target", bg=BG, fg=TEXT).pack(side="left")
        self.code_target = tk.StringVar(value="project")
        ttk.Combobox(top, textvariable=self.code_target, values=["project", "core"], state="readonly", width=10).pack(side="left", padx=6)
        tk.Label(top, text="Core changes require review, backup, validation, and restart.", bg=BG, fg=MUTED).pack(side="left", padx=8)

        body = ttk.Frame(self.code_tab)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        self.code_files = tk.Listbox(left, width=38, bg=PANEL, fg=TEXT, selectbackground=EMERALD, exportselection=False)
        self.code_files.pack(fill="y", expand=True)
        ttk.Button(left, text="Refresh Files", command=self.refresh_code_files).pack(fill="x", pady=(6,0))
        ttk.Button(left, text="View Selected", command=self.view_code_file).pack(fill="x")

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(8,0))
        tk.Label(right, text="Change request", bg=BG, fg=TEXT).pack(anchor="w")
        self.code_request = self._text_widget(right, height=6)
        self.code_request.pack(fill="x")
        buttons = ttk.Frame(right)
        buttons.pack(fill="x", pady=5)
        ttk.Button(buttons, text="Draft Proposal", command=self.draft_code_proposal).pack(side="left", padx=2)
        ttk.Button(buttons, text="Validate Current", command=self.validate_code).pack(side="left", padx=2)
        ttk.Button(buttons, text="Apply Reviewed Proposal", command=self.apply_code_proposal).pack(side="left", padx=2)
        self.code_output = self._text_widget(right)
        self.code_output.pack(fill="both", expand=True)
        self.current_code_proposal = None
        self.refresh_code_files()

    def refresh_code_files(self):
        if not hasattr(self, "code_files"):
            return
        self.code_files.delete(0, "end")
        try:
            for rel in self.coding.list_files(self.code_target.get()):
                self.code_files.insert("end", rel)
        except Exception as exc:
            self.code_output.delete("1.0", "end")
            self.code_output.insert("1.0", f"ERROR: {exc}")

    def view_code_file(self):
        sel = self.code_files.curselection()
        if not sel:
            return
        rel = self.code_files.get(sel[0])
        try:
            text = self.coding.read_file(rel, self.code_target.get())
            self.code_output.delete("1.0", "end")
            self.code_output.insert("1.0", f"FILE: {rel}\n\n{text}")
        except Exception as exc:
            messagebox.showerror("Code Studio", str(exc))

    def draft_code_proposal(self):
        if self.busy:
            return
        request = self.code_request.get("1.0", "end").strip()
        if not request:
            messagebox.showinfo("Code Studio", "Describe the code change first.")
            return
        target = self.code_target.get()
        selected = [self.code_files.get(i) for i in self.code_files.curselection()]
        if target == "core" and not messagebox.askyesno("Core Upgrade Proposal", "Allow the model to inspect approved core source and draft a change? It will not apply anything yet."):
            return
        self.busy = True
        self._set_status("Coding model is drafting a proposal...")
        def done(result):
            self.busy = False
            if isinstance(result, str):
                self.code_output.delete("1.0", "end")
                self.code_output.insert("1.0", result)
                return
            self.current_code_proposal = result.get("id")
            self.code_output.delete("1.0", "end")
            self.code_output.insert("1.0", json.dumps(result, indent=2))
            self._set_status(f"Proposal {self.current_code_proposal} ready for review")
        self._run_bg(lambda: self.coding.propose(request, target, selected), done)

    def validate_code(self):
        target = self.code_target.get()
        self._set_status(f"Validating {target} code...")
        def done(result):
            self.code_output.delete("1.0", "end")
            self.code_output.insert("1.0", json.dumps(result, indent=2) if not isinstance(result, str) else result)
            self._set_status("Validation passed" if isinstance(result, dict) and result.get("passed") else "Validation failed")
        self._run_bg(lambda: self.coding.validate(target), done)

    def apply_code_proposal(self):
        proposal_id = self.current_code_proposal
        if not proposal_id:
            messagebox.showinfo("Code Studio", "Draft and review a proposal first.")
            return
        try:
            proposal = self.coding.load_proposal(proposal_id)
        except Exception as exc:
            messagebox.showerror("Code Studio", str(exc))
            return
        files = "\n".join("• " + f["path"] for f in proposal.get("files", []))
        warning = f"Apply proposal {proposal_id}?\n\n{proposal.get('summary','')}\n\nFiles:\n{files}\n\nA rollback snapshot will be created and fixed validation checks will run."
        if proposal.get("target") == "core":
            warning += "\n\nThis changes VOID OS itself. Restart is required after success."
        if not messagebox.askyesno("Human Approval Required", warning):
            return
        typed = simpledialog.askstring("Confirm Apply", "Type APPLY to authorize this reviewed change:")
        if typed != "APPLY":
            messagebox.showinfo("Code Studio", "Change cancelled.")
            return
        self._set_status("Applying reviewed proposal and validating...")
        def done(result):
            self.code_output.delete("1.0", "end")
            self.code_output.insert("1.0", json.dumps(result, indent=2) if not isinstance(result, str) else result)
            self.refresh_code_files()
            if isinstance(result, dict) and result.get("restart_required"):
                messagebox.showinfo("Core Upgrade Applied", "Validation passed. Restart VOID OS to load the upgraded core.")
            self._set_status("Proposal applied" if isinstance(result, dict) else "Apply failed")
        self._run_bg(lambda: self.coding.apply(proposal_id), done)

    def _build_factory_tab(self):
        outer = ttk.Frame(self.factory_tab)
        outer.pack(fill="both", expand=True, padx=8, pady=8)

        form = ttk.Frame(outer)
        form.pack(side="left", fill="y", padx=(0, 8))
        tk.Label(form, text="Build a new agent", bg=BG, fg=GOLD, font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))

        tk.Label(form, text="Agent name", bg=BG, fg=TEXT).pack(anchor="w")
        self.factory_name = tk.StringVar()
        ttk.Entry(form, textvariable=self.factory_name, width=34).pack(fill="x", pady=(0, 6))

        tk.Label(form, text="Purpose", bg=BG, fg=TEXT).pack(anchor="w")
        self.factory_purpose = self._text_widget(form, width=38, height=6)
        self.factory_purpose.pack(fill="x", pady=(0, 6))

        tk.Label(form, text="Tools, comma separated", bg=BG, fg=TEXT).pack(anchor="w")
        self.factory_tools = tk.StringVar(value="knowledge_search")
        ttk.Entry(form, textvariable=self.factory_tools, width=34).pack(fill="x", pady=(0, 6))

        tk.Label(form, text="Permissions, comma separated", bg=BG, fg=TEXT).pack(anchor="w")
        self.factory_permissions = tk.StringVar(value="read_workspace, write_workspace, create_proposal")
        ttk.Entry(form, textvariable=self.factory_permissions, width=34).pack(fill="x", pady=(0, 6))

        tk.Label(form, text="Memory", bg=BG, fg=TEXT).pack(anchor="w")
        self.factory_memory = tk.StringVar(value="project")
        ttk.Combobox(form, textvariable=self.factory_memory, values=["none", "session", "project", "persistent"], state="readonly").pack(fill="x", pady=(0, 6))

        tk.Label(form, text="Voice", bg=BG, fg=TEXT).pack(anchor="w")
        self.factory_voice = tk.StringVar(value="professional")
        ttk.Entry(form, textvariable=self.factory_voice, width=34).pack(fill="x", pady=(0, 6))

        tk.Label(form, text="Extra requirements", bg=BG, fg=TEXT).pack(anchor="w")
        self.factory_requirements = self._text_widget(form, width=38, height=5)
        self.factory_requirements.pack(fill="x", pady=(0, 8))

        self.factory_build_btn = ttk.Button(form, text="Build Agent Automatically", command=self.start_agent_factory)
        self.factory_build_btn.pack(fill="x")
        self.factory_apply_btn = ttk.Button(form, text="Install Reviewed Agent", command=self.apply_factory_agent)
        self.factory_apply_btn.pack(fill="x", pady=(4, 0))

        center = ttk.Frame(outer)
        center.pack(side="left", fill="y", padx=(0, 8))
        tk.Label(center, text="Pipeline", bg=BG, fg=TEXT).pack(anchor="w")
        self.factory_stage_list = tk.Listbox(center, width=28, bg=PANEL, fg=TEXT, selectbackground=EMERALD, exportselection=False)
        self.factory_stage_list.pack(fill="y", expand=True)
        for stage in self.agent_factory.STAGES:
            self.factory_stage_list.insert("end", f"○ {stage}")

        right = ttk.Frame(outer)
        right.pack(side="left", fill="both", expand=True)
        tk.Label(right, text="Factory report and generated files", bg=BG, fg=TEXT).pack(anchor="w")
        self.factory_output = self._text_widget(right)
        self.factory_output.pack(fill="both", expand=True)
        self.current_factory_run = None

    def _factory_spec(self):
        split = lambda value: [x.strip() for x in value.split(",") if x.strip()]
        return {
            "name": self.factory_name.get().strip(),
            "purpose": self.factory_purpose.get("1.0", "end").strip(),
            "tools": split(self.factory_tools.get()),
            "permissions": split(self.factory_permissions.get()),
            "memory": self.factory_memory.get(),
            "voice": self.factory_voice.get().strip() or "professional",
            "deployment": "local",
            "requirements": self.factory_requirements.get("1.0", "end").strip(),
        }

    def _update_factory_stage(self, payload):
        stage, info = payload
        stages = list(self.agent_factory.STAGES)
        if stage not in stages:
            return
        idx = stages.index(stage)
        status = info.get("status", "waiting")
        icon = {"running": "◉", "passed": "✓", "failed": "✗", "skipped": "–", "waiting": "○"}.get(status, "○")
        self.factory_stage_list.delete(idx)
        self.factory_stage_list.insert(idx, f"{icon} {stage}: {status}")
        self.factory_stage_list.itemconfig(idx, fg=EMERALD if status == "passed" else (MAGENTA if status == "failed" else TEXT))
        self._set_status(f"Agent Factory: {stage} {status}")

    def start_agent_factory(self):
        if self.busy:
            return
        spec = self._factory_spec()
        if not spec["name"] or not spec["purpose"]:
            messagebox.showinfo("Agent Factory", "Enter an agent name and purpose.")
            return
        if not messagebox.askyesno("Start Agent Factory", "Run Planner, Architect, Coder, Reviewer, Tester, and bounded Repair agents now? Nothing will be installed without your approval."):
            return
        self.busy = True
        self.factory_build_btn.configure(state="disabled")
        self.factory_output.delete("1.0", "end")
        self.factory_output.insert("1.0", "Agent Factory started. The coding team is working...\n")
        for i, stage in enumerate(self.agent_factory.STAGES):
            self.factory_stage_list.delete(i)
            self.factory_stage_list.insert(i, f"○ {stage}")

        def progress(stage, info):
            self._ui_queue.put((self._update_factory_stage, (stage, info)))

        def done(result):
            self.busy = False
            self.factory_build_btn.configure(state="normal")
            self.factory_output.delete("1.0", "end")
            if isinstance(result, str):
                self.factory_output.insert("1.0", result)
                self._set_status("Agent Factory failed")
                return
            self.current_factory_run = result.get("id")
            self.factory_output.insert("1.0", json.dumps(result, indent=2))
            self._set_status(f"Agent {result.get('spec', {}).get('name')} ready for human approval")

        self._run_bg(lambda: self.agent_factory.build(spec, progress), done)

    def apply_factory_agent(self):
        run_id = self.current_factory_run
        if not run_id:
            messagebox.showinfo("Agent Factory", "Build and review an agent first.")
            return
        try:
            state = self.agent_factory.load(run_id)
        except Exception as exc:
            messagebox.showerror("Agent Factory", str(exc))
            return
        files = "\n".join("• " + f["path"] for f in state.get("bundle", {}).get("files", []))
        prompt = f"Install generated agent '{state.get('spec', {}).get('name')}'?\n\nFiles:\n{files}\n\nThe bundle passed fixed review and tests. A rollback backup will be created."
        if not messagebox.askyesno("Human Approval Required", prompt):
            return
        typed = simpledialog.askstring("Confirm Install", "Type INSTALL to authorize this reviewed agent:")
        if typed != "INSTALL":
            messagebox.showinfo("Agent Factory", "Installation cancelled.")
            return
        self._set_status("Installing reviewed agent...")
        def done(result):
            self.factory_output.delete("1.0", "end")
            self.factory_output.insert("1.0", json.dumps(result, indent=2) if not isinstance(result, str) else result)
            if isinstance(result, dict):
                self.agents.reload()
                self.refresh_all()
                messagebox.showinfo("Agent Installed", "The generated agent is installed and available in the Agents tab.")
                self._set_status("Generated agent installed")
            else:
                self._set_status("Agent installation failed")
        self._run_bg(lambda: self.agent_factory.apply(run_id), done)

    def _build_system_tab(self):
        self.sysout = self._text_widget(self.system_tab)
        self.sysout.pack(fill="both", expand=True, padx=8, pady=8)
        btns = ttk.Frame(self.system_tab)
        btns.pack(pady=4)
        ttk.Button(btns, text="Refresh System Report", command=self.system_report).pack(side="left", padx=4)
        ttk.Button(btns, text="Start Local API", command=self.start_api_now).pack(side="left", padx=4)
        ttk.Button(btns, text="Check Ollama Connection", command=self._check_connection).pack(side="left", padx=4)

    # ------------------------------------------------------------- helpers
    def _ensure_sample_workflow(self):
        if self.workflows.list():
            return
        self.workflows.save("custom_agent_offer", [
            {"type": "input", "text": "Describe the customer and painful business problem."},
            {"type": "team", "prompt": "Design a complete custom AI agent offer. Include: executive summary, customer problem, proposed agent, workflow, integrations, deliverables, implementation timeline, three pricing tiers, monthly support, privacy and approval safeguards, success metrics, concise sales pitch, and exact next action. Use facts from INPUT only; mark missing facts as To confirm. Finish every section."},
            {"type": "save", "filename": "custom_agent_offer.md"},
        ])

    def _run_bg(self, fn, done):
        """Run fn off-thread and marshal its callback through a main-thread queue.

        Tkinter methods, including ``after()``, are never called from a worker
        thread. This avoids ``RuntimeError: main thread is not in main loop``
        on Windows Store Python and while the window is closing.
        """
        def worker():
            try:
                result = fn()
            except Exception as e:
                result = f"ERROR: {e}"
            self._ui_queue.put((done, result))
        threading.Thread(target=worker, daemon=True).start()

    def _drain_ui_queue(self):
        if self._closing:
            return
        try:
            while True:
                done, result = self._ui_queue.get_nowait()
                try:
                    done(result)
                except Exception as e:
                    self._set_status(f"UI callback error: {e}")
        except queue.Empty:
            pass
        try:
            self.after(50, self._drain_ui_queue)
        except tk.TclError:
            pass

    def _on_close(self):
        self.autopilot_stop.set()
        self._closing = True
        try:
            if self.api:
                self.api.shutdown()
                self.api.server_close()
        except Exception:
            pass
        self.destroy()

    def _set_status(self, text: str):
        self.status.set(text)

    def _check_connection(self):
        def check():
            return self.router.is_available()
        def done(ok):
            self._set_status(("Ollama: connected" if ok else "Ollama: unreachable -- start it, then re-check") +
                              f"  |  project: {self.cfg.get('active_project')}  |  models: {self.cfg.get('models')}")
        self._run_bg(check, done)

    # ---------------------------------------------------------------- chat
    def ask(self):
        if self.busy:
            return
        q = self.chatq.get().strip()
        if not q:
            return
        self.chatout.insert("end", "\nYOU: " + q + "\n")
        self.chatq.set("")
        self.busy = True
        self.ask_btn.state(["disabled"])
        def done(r):
            self.chatout.insert("end", "FORGE: " + r + "\n")
            self.chatout.see("end")
            self.busy = False
            self.ask_btn.state(["!disabled"])
        self._run_bg(lambda: self.router.generate(q), done)

    def team(self):
        if self.busy:
            return
        q = self.chatq.get().strip()
        if not q:
            return
        self.chatout.insert("end", "\nTEAM GOAL: " + q + "\n")
        self.chatq.set("")
        self.busy = True
        self.team_btn.state(["disabled"])
        def done(r):
            text = r if isinstance(r, str) else r.get("synthesis", str(r))
            self.chatout.insert("end", "SYNTHESIS: " + text + "\n")
            self.chatout.see("end")
            self.busy = False
            self.team_btn.state(["!disabled"])
        self._run_bg(lambda: self.agents.team_run(q), done)

    # ------------------------------------------------------------- general
    def refresh_all(self):
        self.wflist.delete(0, "end")
        for name in self.workflows.list():
            self.wflist.insert("end", name)
        self.agentlist.delete(0, "end")
        for name in self.agents.agents:
            self.agentlist.insert("end", name)
        self.system_report()

    # ----------------------------------------------------- workflow canvas
    def new_workflow(self):
        name = simpledialog.askstring("Workflow", "Name:")
        if not name:
            return
        self.current_workflow = name
        self.nodes = []
        self.node_positions = {}
        self.workflows.save(name, self.nodes)
        self.draw()
        self.refresh_all()

    def load_workflow(self):
        sel = self.wflist.curselection()
        if not sel:
            return
        self.current_workflow = self.wflist.get(sel[0])
        self.nodes = self.workflows.load(self.current_workflow)["nodes"]
        self.node_positions = {}
        self.draw()

    def rename_workflow(self):
        if not self.current_workflow:
            messagebox.showinfo("Workflow", "Load a workflow first.")
            return
        new_name = simpledialog.askstring("Rename", "New name:", initialvalue=self.current_workflow)
        if not new_name or new_name == self.current_workflow:
            return
        self.workflows.save(new_name, self.nodes)
        self.workflows.delete(self.current_workflow)
        self.current_workflow = new_name
        self.refresh_all()

    def delete_workflow(self):
        sel = self.wflist.curselection()
        if not sel:
            return
        name = self.wflist.get(sel[0])
        if not messagebox.askyesno("Delete Workflow", f"Delete '{name}'? This cannot be undone."):
            return
        self.workflows.delete(name)
        if self.current_workflow == name:
            self.current_workflow = None
            self.nodes = []
            self.draw()
        self.refresh_all()

    def _prompt_node_fields(self, kind: str, existing: dict = None) -> dict:
        """Ask the user for a node's fields, pre-filled if editing."""
        existing = existing or {}
        node = {"type": kind}
        if kind == "input":
            node["text"] = simpledialog.askstring("Input", "Default input:", initialvalue=existing.get("text", "")) or ""
        elif kind == "agent":
            node["agent"] = simpledialog.askstring(
                "Agent", "Agent name:",
                initialvalue=existing.get("agent") or next(iter(self.agents.agents), "")) or ""
            node["prompt"] = simpledialog.askstring("Prompt", "Agent instruction:", initialvalue=existing.get("prompt", "")) or ""
        elif kind == "team":
            node["prompt"] = simpledialog.askstring("Team", "Team instruction:", initialvalue=existing.get("prompt", "")) or ""
        elif kind == "save":
            node["filename"] = simpledialog.askstring(
                "Save", "Filename:", initialvalue=existing.get("filename", "output.md")) or "output.md"
        elif kind == "plugin":
            node["plugin"] = simpledialog.askstring("Plugin", "Plugin id:", initialvalue=existing.get("plugin", "")) or ""
            node["tool"] = simpledialog.askstring("Plugin", "Tool function name:", initialvalue=existing.get("tool", "")) or ""
        elif kind == "approval":
            node["reason"] = simpledialog.askstring(
                "Approval", "Reason:", initialvalue=existing.get("reason", "Human approval required")) or "Human approval required"
        return node

    def add_node(self, kind: str):
        if not self.current_workflow:
            messagebox.showinfo("Workflow", "Create or load a workflow first.")
            return
        node = self._prompt_node_fields(kind)
        self.nodes.append(node)
        self.workflows.save(self.current_workflow, self.nodes)
        self.draw()

    def _edit_node(self, index: int):
        if index >= len(self.nodes):
            return
        kind = self.nodes[index]["type"]
        self.nodes[index] = self._prompt_node_fields(kind, self.nodes[index])
        self.workflows.save(self.current_workflow, self.nodes)
        self.draw()

    def _delete_node(self, index: int):
        if index >= len(self.nodes):
            return
        self.nodes.pop(index)
        self.node_positions.pop(index, None)
        self.workflows.save(self.current_workflow, self.nodes)
        self.draw()

    # canvas layout / interaction ------------------------------------
    def _default_position(self, i: int):
        col, row = divmod(i, 4)
        return 80 + row * 225, 90 + col * 120

    def _node_at(self, x: int, y: int):
        for i in range(len(self.nodes)):
            nx, ny = self.node_positions.get(i, self._default_position(i))
            if nx - 15 <= x <= nx + 145 and ny - 35 <= y <= ny + 35:
                return i
        return None

    def draw(self):
        self.canvas.delete("all")
        for i, node in enumerate(self.nodes):
            x, y = self.node_positions.get(i, self._default_position(i))
            if i:
                px, py = self.node_positions.get(i - 1, self._default_position(i - 1))
                self.canvas.create_line(px + 145, py, x - 15, y, fill=GOLD, width=2, arrow="last")
            color = NODE_COLORS.get(node["type"], EMERALD)
            self.canvas.create_rectangle(x - 15, y - 35, x + 145, y + 35, fill="#1c1c22", outline=color, width=2)
            self.canvas.create_text(x + 65, y - 12, text=node["type"].upper(), fill=color, font=("Segoe UI", 11, "bold"))
            label = str(node.get("agent") or node.get("plugin") or node.get("filename") or node.get("prompt") or node.get("text", ""))[:20]
            self.canvas.create_text(x + 65, y + 12, text=label, fill=TEXT)

    def _on_canvas_press(self, event):
        self.drag_index = self._node_at(event.x, event.y)

    def _on_canvas_drag(self, event):
        if self.drag_index is not None:
            self.node_positions[self.drag_index] = (event.x, event.y)
            self.draw()

    def _on_canvas_release(self, event):
        self.drag_index = None

    def _on_canvas_double_click(self, event):
        i = self._node_at(event.x, event.y)
        if i is not None:
            self._edit_node(i)

    def _on_canvas_right_click(self, event):
        i = self._node_at(event.x, event.y)
        if i is None:
            return
        menu = tk.Menu(self, tearoff=0, bg=PANEL, fg=TEXT)
        menu.add_command(label="Edit", command=lambda: self._edit_node(i))
        menu.add_command(label="Delete", command=lambda: self._delete_node(i))
        menu.tk_popup(event.x_root, event.y_root)

    def run_workflow(self):
        if not self.current_workflow:
            messagebox.showinfo("Workflow", "Create or load a workflow first.")
            return
        run_input = simpledialog.askstring("Run Workflow", "Runtime input:") or ""
        self.chatout.insert("end", f"\nWORKFLOW {self.current_workflow} RUNNING...\n")
        def done(r):
            if isinstance(r, str):
                self.chatout.insert("end", r + "\n")
            else:
                if r.get("error"):
                    self.chatout.insert("end", f"WORKFLOW ERROR: {r['error']}\n")
                final = r.get("final_value", "")
                saved = r.get("saved_files", [])
                self.chatout.insert("end", "WORKFLOW COMPLETE\n")
                if final:
                    self.chatout.insert("end", str(final) + "\n")
                for path in saved:
                    self.chatout.insert("end", f"Saved: {path}\n")
            self.chatout.see("end")
        self._run_bg(lambda: self.workflows.run(self.current_workflow, run_input), done)

    # -------------------------------------------------------------- agents
    def show_agent(self, _event=None):
        sel = self.agentlist.curselection()
        if not sel:
            return
        name = self.agentlist.get(sel[0])
        self.agentview.delete("1.0", "end")
        self.agentview.insert("1.0", json.dumps(self.agents.agents[name], indent=2))

    # -------------------------------------------------------------- system
    def system_report(self):
        report = {
            "version": self.cfg.get("version"),
            "models": self.cfg.get("models"),
            "agents": list(self.agents.agents),
            "agent_load_errors": self.agents.load_errors,
            "workflows": self.workflows.list(),
            "plugins": {k: {"enabled": v.get("enabled", False)} for k, v in self.plugins.plugins.items()},
            "plugin_load_errors": self.plugins.load_errors,
            "active_project": self.cfg.get("active_project"),
            "api": self.cfg.get("api"),
            "coding": self.cfg.get("coding"),
            "code_proposals": len(self.coding.list_proposals()),
        }
        self.sysout.delete("1.0", "end")
        self.sysout.insert("1.0", json.dumps(report, indent=2))

    def start_api_now(self):
        if self.api:
            messagebox.showinfo("API", f"Already running at http://{self.cfg['api']['host']}:{self.cfg['api']['port']}")
            return
        self.api = start_api(self, self.cfg["api"]["host"], self.cfg["api"]["port"])
        self.cfg["api"]["enabled"] = True
        save(self.cfg)
        messagebox.showinfo("API", f"Running at http://{self.cfg['api']['host']}:{self.cfg['api']['port']}")
