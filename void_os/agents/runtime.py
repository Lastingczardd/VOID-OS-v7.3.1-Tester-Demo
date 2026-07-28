"""Loads JSON-defined agents and runs them (individually or as a team)
against the model router."""

import json
from ..kernel.paths import DATA
from ..kernel.witness import log

ALLOWED_PERMISSIONS = {
    "read_codex",
    "read_datasets",
    "read_workspace",
    "write_workspace",
    "create_proposal",
    "use_images",
    "analyze_audio",
}


class AgentRuntime:
    def __init__(self, router):
        self.router = router
        self.agents = {}
        self.load_errors = []
        self.reload()

    def reload(self):
        """(Re)load every agent JSON file under data/agents/. Unknown
        permissions are dropped rather than trusted, and a bad file is
        skipped (with the reason recorded) instead of crashing the app."""
        self.agents = {}
        self.load_errors = []
        for path in sorted((DATA / "agents").glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                raw["permissions"] = [p for p in raw.get("permissions", []) if p in ALLOWED_PERMISSIONS]
                name = raw.get("name", path.stem)
                self.agents[name] = raw
            except Exception as e:
                self.load_errors.append(f"{path.name}: {e}")
        return self.agents

    def run(self, name: str, goal: str, context: str = "") -> str:
        if name not in self.agents:
            available = ", ".join(sorted(self.agents)) or "(none loaded)"
            raise KeyError(f"No agent named '{name}'. Available agents: {available}")
        agent = self.agents[name]
        prompt = (
            f"ROLE: {agent.get('role', name)}\n"
            f"STYLE: {agent.get('style', 'clear and useful')}\n"
            f"PERMISSIONS: {', '.join(agent.get('permissions', []))}\n"
            f"GOAL: {goal}\n"
            f"CONTEXT: {context}\n"
            "Return a concrete contribution. Do not claim actions you did not perform."
        )
        try:
            result = self.router.generate(
                prompt,
                "reasoning",
                max_tokens=self.router.cfg.get("agent_output_tokens", 520),
            )
        except Exception as e:
            log("agent_run_failed", {"agent": name, "goal": goal[:160], "error": str(e)})
            raise
        log("agent_run", {"agent": name, "goal": goal[:160], "output_chars": len(result)})
        return result

    def team_run(self, goal: str, names: list = None) -> dict:
        """Run agents in sequence, each seeing prior contributions, then
        synthesize a single answer from all of them."""
        names = names or list(self.agents)[:5]
        if not names:
            raise ValueError("No agents available to run as a team.")
        contributions = []
        for n in names:
            context = "Previous contributions:\n" + "\n\n".join(
                f"{x}: {y[:900]}" for x, y in contributions
            )
            contributions.append((n, self.run(n, goal, context)))
        synthesis_prompt = (
            "Synthesize these agent contributions into one complete, practical answer.\n"
            "Use concise sections and finish every requested section. Do not stop mid-sentence.\n"
            f"GOAL: {goal}\n"
            + "\n\n".join(f"[{n}]\n{r}" for n, r in contributions)
        )
        synthesis = self.router.generate(
            synthesis_prompt,
            "reasoning",
            max_tokens=self.router.cfg.get("synthesis_output_tokens", 1800),
        )

        continuations = 0
        max_continuations = int(self.router.cfg.get("max_auto_continuations", 1))
        while continuations < max_continuations and self._looks_incomplete(synthesis):
            continuation_prompt = (
                "Continue the document below from the exact point where it stopped. "
                "Return only the missing continuation, without repeating headings or prior text. "
                "Finish all remaining requested sections and end cleanly.\n\n"
                "DOCUMENT SO FAR:\n" + synthesis
            )
            extra = self.router.generate(
                continuation_prompt,
                "reasoning",
                max_tokens=self.router.cfg.get("continuation_output_tokens", 900),
            )
            if not extra.strip():
                break
            synthesis = self._merge_continuation(synthesis, extra)
            continuations += 1

        return {
            "contributions": contributions,
            "synthesis": synthesis,
            "auto_continuations": continuations,
        }

    @staticmethod
    def _looks_incomplete(text: str) -> bool:
        """Conservative truncation detector for local model outputs."""
        clean = (text or "").rstrip()
        if not clean:
            return False
        last = clean[-1]
        if last not in ".!?)]}`\"'":
            return True
        tail = clean[-120:].lower()
        unfinished = (
            "continued", "to be continued", "next,", "and then", "including:",
            "such as:", "the following:", "deliverables:", "workflow:",
        )
        return any(tail.endswith(x) for x in unfinished)

    @staticmethod
    def _merge_continuation(original: str, extra: str) -> str:
        """Join a continuation while removing a small repeated overlap."""
        left = original.rstrip()
        right = extra.lstrip()
        max_overlap = min(240, len(left), len(right))
        for size in range(max_overlap, 19, -1):
            if left[-size:].lower() == right[:size].lower():
                right = right[size:].lstrip()
                break
        return left + ("\n" if right else "") + right
