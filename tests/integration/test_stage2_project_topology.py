import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "research-agent-team"
SCRIPT = PLUGIN_ROOT / "scripts" / "rat_plugin_cli.py"


class Stage2ProjectTopologyTests(unittest.TestCase):
    def run_cli(self, command_name: str, payload: dict, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "command", command_name, "--payload-json", json.dumps(payload)],
            cwd=PLUGIN_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            self.fail(f"{command_name} failed\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

    def parse_json(self, result: subprocess.CompletedProcess[str]) -> dict:
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"invalid JSON output: {exc}\nstdout={result.stdout}\nstderr={result.stderr}")

    def create_project(self, root_path: Path) -> dict:
        result = self.run_cli(
            "create_project",
            {
                "name": "Topology Demo",
                "root_path": str(root_path),
                "initial_charter_text": "Investigate deterministic local research orchestration.",
            },
        )
        payload = self.parse_json(result)
        self.assertTrue(payload["ok"], payload)
        return payload["result"]

    def test_create_project_then_open_from_codex_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            created = self.create_project(project_root)

            self.assertEqual(created["project"]["name"], "Topology Demo")
            self.assertEqual(created["project"]["status"], "active")
            self.assertEqual(created["topology"]["active_slot_ids"], ["supervisor", "senior-01"])

            required_paths = [
                "project.yaml",
                "state/project.json",
                "state/topology/current.json",
                "state/slots/supervisor.json",
                "state/slots/senior-01.json",
                "state/budgets/current.json",
                "state/policies/staffing.json",
                "state/policies/execution.json",
                "state/policies/budget.json",
                "state/policies/approvals.json",
                "state/adapters/config.json",
                "state/adapters/health.json",
                "state/hooks/config.json",
                "shared/artifacts/initial-charter.md",
            ]
            for relative_path in required_paths:
                self.assertTrue((project_root / relative_path).exists(), relative_path)

            for slot_id in ["supervisor", "senior-01"]:
                for relative_dir in ["checkpoints", "activations", "workspace", "kb", "inbox", "outbox"]:
                    self.assertTrue((project_root / "agents" / slot_id / relative_dir).is_dir(), f"{slot_id}/{relative_dir}")

            event_lines = list((project_root / "state" / "events").glob("*.jsonl"))
            self.assertEqual(len(event_lines), 1)
            self.assertIn("project.created", event_lines[0].read_text(encoding="utf-8"))

            opened = self.parse_json(self.run_cli("open_project", {"root_path": str(project_root)}))
            self.assertTrue(opened["ok"], opened)
            self.assertEqual(opened["result"]["project"]["name"], "Topology Demo")
            self.assertEqual(opened["result"]["topology"]["slot_count"], 2)
            self.assertEqual(opened["result"]["adapter_health"]["graph"]["status"], "degraded")
            self.assertEqual(opened["result"]["pending_approval_count"], 0)

    def test_missing_project_open_does_not_create_lock_and_later_create_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "typo-project"

            opened = self.parse_json(self.run_cli("open_project", {"root_path": str(project_root)}, check=False))
            self.assertFalse(opened["ok"], opened)
            self.assertEqual(opened["error"]["code"], "project_not_found")
            self.assertFalse((project_root / "state" / "locks" / "project.lock").exists())

            created = self.create_project(project_root)
            self.assertEqual(created["project"]["name"], "Topology Demo")

    def test_null_initial_charter_text_is_rejected_before_project_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"

            result = self.run_cli(
                "create_project",
                {
                    "name": "Bad Charter",
                    "root_path": str(project_root),
                    "initial_charter_text": None,
                },
                check=False,
            )
            payload = self.parse_json(result)
            self.assertFalse(payload["ok"], payload)
            self.assertEqual(payload["error"]["code"], "invalid_payload")
            self.assertFalse(project_root.exists())

            created = self.create_project(project_root)
            self.assertEqual(created["project"]["name"], "Topology Demo")

    def test_existing_file_root_path_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "not-a-directory"
            project_root.write_text("already a file\n", encoding="utf-8")

            result = self.run_cli(
                "create_project",
                {
                    "name": "Bad Root",
                    "root_path": str(project_root),
                },
                check=False,
            )

            payload = self.parse_json(result)
            self.assertFalse(payload["ok"], payload)
            self.assertEqual(payload["error"]["code"], "invalid_root_path")
            self.assertEqual(result.stderr, "")

    def test_topology_mutations_preserve_surviving_state_and_reject_invalid_retirement(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            original_senior = json.loads((project_root / "state" / "slots" / "senior-01.json").read_text(encoding="utf-8"))

            added_senior = self.parse_json(self.run_cli("add_senior", {"root_path": str(project_root)}))
            self.assertTrue(added_senior["ok"], added_senior)
            self.assertEqual(added_senior["result"]["slot"]["slot_id"], "senior-02")

            added_junior = self.parse_json(
                self.run_cli("add_junior", {"root_path": str(project_root), "parent_slot_id": "senior-01"})
            )
            self.assertTrue(added_junior["ok"], added_junior)
            self.assertEqual(added_junior["result"]["slot"]["slot_id"], "junior-01")

            surviving_senior = json.loads((project_root / "state" / "slots" / "senior-01.json").read_text(encoding="utf-8"))
            self.assertEqual(surviving_senior["created_at"], original_senior["created_at"])
            self.assertIn("junior-01", surviving_senior["descendant_slot_ids"])

            rejected = self.parse_json(
                self.run_cli("retire_senior", {"root_path": str(project_root), "slot_id": "senior-01"}, check=False)
            )
            self.assertFalse(rejected["ok"], rejected)
            self.assertEqual(rejected["error"]["code"], "active_child_slots")

            retired_junior = self.parse_json(
                self.run_cli("retire_junior", {"root_path": str(project_root), "slot_id": "junior-01"})
            )
            self.assertTrue(retired_junior["ok"], retired_junior)

            retired_senior = self.parse_json(
                self.run_cli("retire_senior", {"root_path": str(project_root), "slot_id": "senior-01"})
            )
            self.assertTrue(retired_senior["ok"], retired_senior)
            topology = json.loads((project_root / "state" / "topology" / "current.json").read_text(encoding="utf-8"))
            self.assertIn("senior-02", topology["active_slot_ids"])
            self.assertIn("senior-01", topology["retired_slot_ids"])
            self.assertIn("junior-01", topology["retired_slot_ids"])

    def test_lifecycle_commands_update_state_and_emit_auditable_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            switched = self.parse_json(
                self.run_cli("switch_operating_mode", {"root_path": str(project_root), "operating_mode": "semi_autonomous"})
            )
            self.assertTrue(switched["ok"], switched)
            self.assertEqual(switched["result"]["project"]["operating_mode"], "semi_autonomous")

            paused = self.parse_json(self.run_cli("pause_project", {"root_path": str(project_root)}))
            self.assertTrue(paused["ok"], paused)
            self.assertEqual(paused["result"]["project"]["status"], "paused")

            resumed = self.parse_json(self.run_cli("resume_project", {"root_path": str(project_root)}))
            self.assertTrue(resumed["ok"], resumed)
            self.assertEqual(resumed["result"]["project"]["status"], "active")

            event_text = "\n".join(path.read_text(encoding="utf-8") for path in (project_root / "state" / "events").glob("*.jsonl"))
            self.assertIn("project.mode_switched", event_text)
            self.assertIn("project.paused", event_text)
            self.assertIn("project.resumed", event_text)

    def test_invalid_staffing_operations_return_structured_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            bad_parent = self.parse_json(
                self.run_cli("add_junior", {"root_path": str(project_root), "parent_slot_id": "supervisor"}, check=False)
            )
            self.assertFalse(bad_parent["ok"], bad_parent)
            self.assertEqual(bad_parent["error"]["code"], "invalid_parent_role")

            bad_retire = self.parse_json(
                self.run_cli("retire_junior", {"root_path": str(project_root), "slot_id": "supervisor"}, check=False)
            )
            self.assertFalse(bad_retire["ok"], bad_retire)
            self.assertEqual(bad_retire["error"]["code"], "invalid_slot_role")

    def test_default_staffing_policy_caps_seniors_at_three(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            second = self.parse_json(self.run_cli("add_senior", {"root_path": str(project_root)}))
            self.assertTrue(second["ok"], second)
            self.assertEqual(second["result"]["slot"]["slot_id"], "senior-02")

            third = self.parse_json(self.run_cli("add_senior", {"root_path": str(project_root)}))
            self.assertTrue(third["ok"], third)
            self.assertEqual(third["result"]["slot"]["slot_id"], "senior-03")

            rejected = self.parse_json(self.run_cli("add_senior", {"root_path": str(project_root)}, check=False))
            self.assertFalse(rejected["ok"], rejected)
            self.assertEqual(rejected["error"]["code"], "staffing_cap_exceeded")

    def test_default_staffing_policy_caps_juniors_per_senior_at_three(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            for expected_slot_id in ["junior-01", "junior-02", "junior-03"]:
                added = self.parse_json(
                    self.run_cli("add_junior", {"root_path": str(project_root), "parent_slot_id": "senior-01"})
                )
                self.assertTrue(added["ok"], added)
                self.assertEqual(added["result"]["slot"]["slot_id"], expected_slot_id)

            rejected = self.parse_json(
                self.run_cli("add_junior", {"root_path": str(project_root), "parent_slot_id": "senior-01"}, check=False)
            )
            self.assertFalse(rejected["ok"], rejected)
            self.assertEqual(rejected["error"]["code"], "staffing_cap_exceeded")


if __name__ == "__main__":
    unittest.main()
