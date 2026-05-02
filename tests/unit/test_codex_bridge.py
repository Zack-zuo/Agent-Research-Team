import tempfile
import unittest
from pathlib import Path

from research_agent_team.platform.codex.bridge import (
    interpret_request,
    plan_launches_for_result,
    render_launch_prompt_for_request,
    run_activation_callback,
    run_command,
)


class CodexBridgeTests(unittest.TestCase):
    def create_project(self, root: Path) -> dict:
        return run_command(
            "create_project",
            {
                "name": "Bridge Demo",
                "root_path": str(root),
                "initial_charter_text": "Bridge test project.",
            },
        )

    def assign_simple_task(self, root: Path) -> dict:
        return run_command(
            "assign_task",
            {
                "root_path": str(root),
                "requester_slot_id": "supervisor",
                "owner_slot_id": "senior-01",
                "title": "Review notes",
                "description": "Review shared notes and write a summary.",
                "success_criteria": ["Write summary"],
                "input_path_roots": ["shared/raw"],
                "expected_output_types": ["markdown"],
            },
        )

    def test_run_command_returns_existing_json_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"

            created = self.create_project(root)

            self.assertTrue(created["ok"], created)
            self.assertEqual(created["result"]["project"]["root_path"], str(root.resolve()))

    def test_run_command_returns_structured_command_error(self) -> None:
        result = run_command("open_project", {})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_payload")
        self.assertIn("root_path", result["error"]["message"])

    def test_plan_launches_for_result_wraps_existing_orchestrator(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            assigned = self.assign_simple_task(root)

            planned = plan_launches_for_result(str(root), assigned, source_command="assign_task")

            self.assertTrue(planned["ok"], planned)
            self.assertEqual(planned["result"]["auto_launch_count"], 1)
            self.assertEqual(planned["result"]["decisions"][0]["action"], "auto_launch")

    def test_render_launch_prompt_for_request_wraps_runtime_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            assigned = self.assign_simple_task(root)

            rendered = render_launch_prompt_for_request(str(root), assigned["result"]["launch_request"])

            self.assertTrue(rendered["ok"], rendered)
            self.assertIn("Activation ID:", rendered["result"]["prompt"])
            self.assertIn("Task briefing:", rendered["result"]["prompt"])

    def test_run_activation_callback_returns_next_launch_request_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            assigned = self.assign_simple_task(root)
            activation_id = assigned["result"]["launch_request"]["activation_id"]

            marked = run_activation_callback("mark-running", str(root), activation_id)

            self.assertTrue(marked["ok"], marked)
            self.assertEqual(marked["result"]["activation_id"], activation_id)

    def test_interpret_request_returns_command_plan_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)

            interpreted = interpret_request(
                "show current progress",
                {"root_path": str(root), "confirmation_mode": "conservative"},
            )

            self.assertTrue(interpreted["ok"], interpreted)
            self.assertEqual(interpreted["result"]["command_name"], "request_status")
            self.assertTrue(interpreted["result"]["ready_for_execution"])


if __name__ == "__main__":
    unittest.main()
