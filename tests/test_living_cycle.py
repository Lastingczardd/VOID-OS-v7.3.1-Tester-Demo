import json
import tempfile
import unittest
from pathlib import Path

from void_os.autopilot.cycle_projects import CycleProjectManager
from void_os.autopilot.implementation import AutonomousImplementationController


class FakeCodingStudio:
    def __init__(self):
        self.selected_files = None
        self.applied = None

    def list_files(self, target):
        return []

    def propose(self, instruction, target, selected_files=None):
        self.selected_files = selected_files
        return {
            "id": "proposal-1",
            "risk": "low",
            "summary": "Bootstrap project",
            "files": [{"path": "main.py", "content": "print('ready')\n"}],
        }

    def apply(self, proposal_id):
        self.applied = proposal_id
        return {"backup": "backup-1", "checks": {"passed": True}}


class LivingCycleTests(unittest.TestCase):
    def test_empty_project_bootstraps_instead_of_skipping(self):
        cfg = {"autopilot": {"implement_project_upgrades": True}}
        coding = FakeCodingStudio()
        controller = AutonomousImplementationController(cfg, coding)
        result = controller.run_once("Create a useful starter", "No files", "Build main.py")
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(coding.selected_files, [])
        self.assertEqual(coding.applied, "proposal-1")

    def test_promotion_keeps_code_and_learning_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "default"
            (source / "code").mkdir(parents=True)
            (source / "code" / "main.py").write_text("x = 1\n", encoding="utf-8")
            (source / "autopilot").mkdir()
            (source / "autopilot" / "opportunities.json").write_text('{"a": {"attempts": 2}}', encoding="utf-8")
            (source / "autopilot" / "checkpoint.json").write_text('{}', encoding="utf-8")
            cfg = {"active_project": "default"}
            promotion = CycleProjectManager(cfg, root).promote(source, 1)
            dest = Path(promotion.path)
            self.assertTrue((dest / "code" / "main.py").exists())
            self.assertTrue((dest / "autopilot" / "opportunities.json").exists())
            self.assertFalse((dest / "autopilot" / "checkpoint.json").exists())
            self.assertEqual(cfg["active_project"], promotion.active_project)


if __name__ == "__main__":
    unittest.main()
