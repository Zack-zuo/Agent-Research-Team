import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "research-agent-team"
SCRIPT = PLUGIN_ROOT / "scripts" / "rat_plugin_cli.py"


class RatLaunchOrchestrationCliTests(unittest.TestCase):
    def run_cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=PLUGIN_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            self.fail(f"CLI failed: {' '.join(args)}\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

    def run_command(self, command_name: str, payload: dict) -> dict:
        result = self.run_cli("command", command_name, "--payload-json", json.dumps(payload))
        return json.loads(result.stdout)

    def test_plan_launches_cli_classifies_assign_task_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            created = self.run_command(
                "create_project",
                {"name": "CLI Launch Demo", "root_path": str(root)},
            )
            self.assertTrue(created["ok"], created)
            assigned = self.run_command(
                "assign_task",
                {
                    "root_path": str(root),
                    "requester_slot_id": "supervisor",
                    "owner_slot_id": "senior-01",
                    "title": "Review notes",
                    "description": "Summarize shared notes.",
                    "success_criteria": ["Write summary"],
                },
            )
            self.assertTrue(assigned["ok"], assigned)

            planned = self.run_cli(
                "plan-launches",
                "--root-path",
                str(root),
                "--source-command",
                "assign_task",
                "--payload-json",
                json.dumps(assigned),
            )
            payload = json.loads(planned.stdout)

            self.assertTrue(payload["ok"], payload)
            self.assertEqual(payload["result"]["auto_launch_count"], 1)
            self.assertEqual(payload["result"]["decisions"][0]["action"], "auto_launch")

    def test_plan_launches_cli_requires_source_command(self) -> None:
        result = self.run_cli(
            "plan-launches",
            "--root-path",
            "/tmp/rat-project",
            "--payload-json",
            json.dumps({"ok": True, "result": {}}),
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--source-command", result.stderr)

    def test_execution_start_pending_cli_uses_fake_subagent_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.run_command(
                "create_project",
                {"name": "CLI Execution Demo", "root_path": str(root)},
            )
            assigned = self.run_command(
                "assign_task",
                {
                    "root_path": str(root),
                    "requester_slot_id": "supervisor",
                    "owner_slot_id": "senior-01",
                    "title": "Review notes",
                    "description": "Summarize shared notes.",
                    "success_criteria": ["Write summary"],
                },
            )
            activation_id = assigned["result"]["launch_request"]["activation_id"]

            started = self.run_cli(
                "execution",
                "start-pending",
                "--root-path",
                str(root),
                "--adapter",
                "fake-subagent",
                "--fake-launch-status",
                "spawned",
            )
            payload = json.loads(started.stdout)

            self.assertTrue(payload["ok"], payload)
            self.assertEqual(payload["result"]["started_count"], 1)
            self.assertEqual(payload["result"]["subagent_launch_requests"][0]["activation_id"], activation_id)

            inspected = self.run_cli("execution", "inspect", "--root-path", str(root))
            inspected_payload = json.loads(inspected.stdout)
            self.assertTrue(inspected_payload["ok"], inspected_payload)
            self.assertEqual(inspected_payload["result"]["worker_count"], 1)


if __name__ == "__main__":
    unittest.main()
