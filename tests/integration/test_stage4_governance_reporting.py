import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from research_agent_team.application.artifact_service import index_artifact
from research_agent_team.application.errors import CommandError
from research_agent_team.domain import ArtifactVisibility
from research_agent_team.storage import ProjectLayout


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "research-agent-team"
SCRIPT = PLUGIN_ROOT / "scripts" / "rat_plugin_cli.py"


class Stage4GovernanceReportingTests(unittest.TestCase):
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

    def run_command(self, command_name: str, payload: dict, check: bool = True) -> dict:
        result = self.run_cli("command", command_name, "--payload-json", json.dumps(payload), check=check)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"invalid JSON output: {exc}\nstdout={result.stdout}\nstderr={result.stderr}")

    def run_activation(self, command_name: str, root_path: Path, activation_id: str, payload: dict = None, check: bool = True) -> dict:
        args = [
            "activation",
            command_name,
            "--root-path",
            str(root_path),
            "--activation-id",
            activation_id,
        ]
        if payload is not None:
            args.extend(["--payload-json", json.dumps(payload)])
        result = self.run_cli(*args, check=check)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"invalid JSON output: {exc}\nstdout={result.stdout}\nstderr={result.stderr}")

    def create_project(self, project_root: Path) -> None:
        payload = self.run_command(
            "create_project",
            {
                "name": "Stage 4 Demo",
                "root_path": str(project_root),
                "initial_charter_text": "Governed orchestration project.",
            },
        )
        self.assertTrue(payload["ok"], payload)

    def assign_task(self, project_root: Path, **overrides: object) -> dict:
        payload = {
            "root_path": str(project_root),
            "requester_slot_id": "supervisor",
            "owner_slot_id": "senior-01",
            "title": "Governed task",
            "description": "Produce governed evidence.",
            "success_criteria": ["Publish a governed artifact"],
            "input_artifact_ids": [],
            "input_path_roots": ["shared/raw"],
            "expected_output_types": ["markdown"],
        }
        payload.update(overrides)
        result = self.run_command("assign_task", payload)
        self.assertTrue(result["ok"], result)
        return result["result"]

    def artifact_ids_for_path(self, project_root: Path, relative_path: str) -> list[str]:
        index_path = project_root / "state" / "artifacts" / "index.jsonl"
        artifacts = [
            json.loads(line)
            for line in index_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return [artifact["artifact_id"] for artifact in artifacts if artifact["path"] == relative_path]

    def test_visibility_rules_and_permission_manifest_cover_attached_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            first = self.assign_task(project_root)
            activation_id = first["launch_request"]["activation_id"]
            self.run_activation("mark-running", project_root, activation_id)
            private_path = project_root / "agents" / "senior-01" / "workspace" / "private-note.md"
            private_path.write_text("private evidence\n", encoding="utf-8")
            completed = self.run_activation(
                "complete",
                project_root,
                activation_id,
                {
                    "output_artifacts": [
                        {
                            "path": "agents/senior-01/workspace/private-note.md",
                            "type": "private_note",
                            "visibility": "slot_private",
                        }
                    ]
                },
            )
            self.assertTrue(completed["ok"], completed)
            private_artifact_id = completed["result"]["published_artifact_ids"][0]

            self.run_command("add_junior", {"root_path": str(project_root), "parent_slot_id": "senior-01"})
            denied = self.run_command(
                "assign_task",
                {
                    "root_path": str(project_root),
                    "requester_slot_id": "senior-01",
                    "owner_slot_id": "junior-01",
                    "title": "Use private note",
                    "description": "This should be denied.",
                    "input_artifact_ids": [private_artifact_id],
                },
                check=False,
            )
            self.assertFalse(denied["ok"], denied)
            self.assertEqual(denied["error"]["code"], "artifact_not_visible")

            allowed = self.assign_task(
                project_root,
                requester_slot_id="senior-01",
                owner_slot_id="senior-01",
                input_artifact_ids=[private_artifact_id],
                input_path_roots=[],
            )
            self.assertTrue(allowed["admitted"], allowed)
            permission_path = project_root / allowed["launch_request"]["permissions_manifest_path"]
            bundle_path = project_root / allowed["launch_request"]["bundle_path"]
            permissions = json.loads(permission_path.read_text(encoding="utf-8"))
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            self.assertEqual(permissions["allowed_artifact_ids"], [private_artifact_id])
            self.assertIn("read_attached_private_artifacts", permissions["granted_permissions"])
            self.assertEqual(bundle["allowed_artifact_ids"], [private_artifact_id])

    def test_activation_callbacks_validate_raw_output_artifact_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            assigned = self.assign_task(project_root)
            activation_id = assigned["launch_request"]["activation_id"]
            self.run_activation("mark-running", project_root, activation_id)

            malformed = self.run_activation(
                "complete",
                project_root,
                activation_id,
                {"output_artifact_ids": "artifact-not-a-list"},
                check=False,
            )
            self.assertFalse(malformed["ok"], malformed)
            self.assertEqual(malformed["error"]["code"], "invalid_payload")

            missing = self.run_activation(
                "complete",
                project_root,
                activation_id,
                {"output_artifact_ids": ["artifact-missing"]},
                check=False,
            )
            self.assertFalse(missing["ok"], missing)
            self.assertEqual(missing["error"]["code"], "artifact_not_found")

            checkpoint_path = project_root / "agents" / "senior-01" / "workspace" / "checkpoint-note.md"
            checkpoint_path.write_text("checkpoint evidence\n", encoding="utf-8")
            checkpoint = self.run_activation(
                "checkpoint",
                project_root,
                activation_id,
                {
                    "summary": "Evidence checkpoint.",
                    "resume_instructions": "Resume from checkpoint evidence.",
                    "output_artifacts": [
                        {
                            "path": "agents/senior-01/workspace/checkpoint-note.md",
                            "type": "checkpoint_note",
                            "visibility": "slot_private",
                        }
                    ],
                },
            )
            self.assertTrue(checkpoint["ok"], checkpoint)
            checkpoint_artifact_id = checkpoint["result"]["published_artifact_ids"][0]

            completed = self.run_activation(
                "complete",
                project_root,
                activation_id,
                {"output_artifact_ids": [checkpoint_artifact_id]},
            )
            self.assertTrue(completed["ok"], completed)
            self.assertEqual(completed["result"]["published_artifact_ids"], [checkpoint_artifact_id])

            next_activation_id = completed["result"]["next_launch_request"]
            self.assertIsNone(next_activation_id)

    def test_activation_callbacks_reject_output_artifact_ids_from_other_activations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            first = self.assign_task(project_root, title="First artifact task")
            first_activation_id = first["launch_request"]["activation_id"]
            self.run_activation("mark-running", project_root, first_activation_id)
            first_path = project_root / "agents" / "senior-01" / "workspace" / "first-output.md"
            first_path.write_text("first evidence\n", encoding="utf-8")
            first_completed = self.run_activation(
                "complete",
                project_root,
                first_activation_id,
                {
                    "output_artifacts": [
                        {
                            "path": "agents/senior-01/workspace/first-output.md",
                            "type": "first_output",
                            "visibility": "slot_private",
                        }
                    ]
                },
            )
            self.assertTrue(first_completed["ok"], first_completed)
            first_artifact_id = first_completed["result"]["published_artifact_ids"][0]

            second = self.assign_task(project_root, title="Second artifact task")
            second_activation_id = second["launch_request"]["activation_id"]
            self.run_activation("mark-running", project_root, second_activation_id)

            rejected = self.run_activation(
                "complete",
                project_root,
                second_activation_id,
                {"output_artifact_ids": [first_artifact_id]},
                check=False,
            )
            self.assertFalse(rejected["ok"], rejected)
            self.assertEqual(rejected["error"]["code"], "artifact_activation_mismatch")

    def test_activation_output_artifacts_reject_worker_supplied_artifact_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            assigned = self.assign_task(project_root)
            activation_id = assigned["launch_request"]["activation_id"]
            self.run_activation("mark-running", project_root, activation_id)
            output_path = project_root / "agents" / "senior-01" / "workspace" / "worker-id.md"
            output_path.write_text("worker supplied id\n", encoding="utf-8")

            rejected = self.run_activation(
                "complete",
                project_root,
                activation_id,
                {
                    "output_artifacts": [
                        {
                            "artifact_id": "artifact-forged",
                            "path": "agents/senior-01/workspace/worker-id.md",
                            "type": "forged_output",
                            "visibility": "slot_private",
                        }
                    ]
                },
                check=False,
            )

            self.assertFalse(rejected["ok"], rejected)
            self.assertEqual(rejected["error"]["code"], "invalid_payload")

    def test_index_artifact_rejects_duplicate_explicit_artifact_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            layout = ProjectLayout(project_root)

            first_path = project_root / "shared" / "artifacts" / "first.md"
            second_path = project_root / "shared" / "artifacts" / "second.md"
            first_path.write_text("first\n", encoding="utf-8")
            second_path.write_text("second\n", encoding="utf-8")

            index_artifact(
                layout,
                path="shared/artifacts/first.md",
                artifact_type="manual",
                visibility=ArtifactVisibility.PROJECT_SHARED,
                producing_slot_id="supervisor",
                artifact_id="artifact-fixed",
            )

            with self.assertRaises(CommandError) as raised:
                index_artifact(
                    layout,
                    path="shared/artifacts/second.md",
                    artifact_type="manual",
                    visibility=ArtifactVisibility.PROJECT_SHARED,
                    producing_slot_id="supervisor",
                    artifact_id="artifact-fixed",
                )
            self.assertEqual(raised.exception.code, "artifact_id_conflict")

    def test_generate_report_rejects_invalid_experiment_run_scope_combination(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            result = self.run_command(
                "generate_report",
                {
                    "root_path": str(project_root),
                    "report_type": "status",
                    "scope_type": "experiment_run",
                    "scope_id": "experiment-run-missing",
                },
                check=False,
            )

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["error"]["code"], "invalid_report_scope")

    def test_generate_report_rejects_missing_non_project_scope_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            cases = [
                {"report_type": "topology", "scope_type": "slot", "scope_id": "senior-99"},
                {"report_type": "next_steps", "scope_type": "task", "scope_id": "task-missing"},
                {"report_type": "experiment_summary", "scope_type": "experiment_run", "scope_id": "experiment-run-missing"},
            ]
            for case in cases:
                with self.subTest(case=case):
                    result = self.run_command(
                        "generate_report",
                        {"root_path": str(project_root), **case},
                        check=False,
                    )
                    self.assertFalse(result["ok"], result)
                    self.assertEqual(result["error"]["code"], "scope_not_found")

    def test_budget_override_approval_replay_queues_or_blocks_real_task_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            approved_root = Path(tmpdir) / "approved-project"
            self.create_project(approved_root)
            gated = self.assign_task(approved_root, budget_override={"token_budget": 1000})
            approval_id = gated["pending_approval"]["approval_id"]
            task_id = gated["task"]["task_id"]

            approved = self.run_command(
                "approve_checkpoint",
                {
                    "root_path": str(approved_root),
                    "approval_id": approval_id,
                    "decision_summary": "Budget override accepted.",
                },
            )
            self.assertTrue(approved["ok"], approved)
            self.assertTrue(approved["result"]["applied"], approved)
            self.assertEqual(approved["result"]["affected_task_id"], task_id)
            self.assertIsNotNone(approved["result"]["launch_request"])
            task = json.loads((approved_root / "state" / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
            approval = json.loads((approved_root / "state" / "approvals" / f"{approval_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(task["status"], "admitted")
            self.assertIsNone(task["current_approval_id"])
            self.assertEqual(approval["status"], "approved")

            rejected_root = Path(tmpdir) / "rejected-project"
            self.create_project(rejected_root)
            rejected_gated = self.assign_task(rejected_root, budget_override={"token_budget": 1000})
            rejected_approval_id = rejected_gated["pending_approval"]["approval_id"]
            rejected_task_id = rejected_gated["task"]["task_id"]

            rejected = self.run_command(
                "reject_checkpoint",
                {
                    "root_path": str(rejected_root),
                    "approval_id": rejected_approval_id,
                    "decision_summary": "Budget override rejected.",
                },
            )
            self.assertTrue(rejected["ok"], rejected)
            self.assertFalse(rejected["result"]["applied"], rejected)
            task = json.loads((rejected_root / "state" / "tasks" / f"{rejected_task_id}.json").read_text(encoding="utf-8"))
            slot = json.loads((rejected_root / "state" / "slots" / "senior-01.json").read_text(encoding="utf-8"))
            approval = json.loads(
                (rejected_root / "state" / "approvals" / f"{rejected_approval_id}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(task["status"], "blocked")
            self.assertEqual(task["block_reason"], "approval_rejected")
            self.assertIsNone(task["current_approval_id"])
            self.assertNotIn(rejected_task_id, slot["active_task_ids"])
            self.assertEqual(approval["status"], "rejected")

    def test_staffing_approval_replay_applies_deferred_topology_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            staffing_path = project_root / "state" / "policies" / "staffing.json"
            staffing = json.loads(staffing_path.read_text(encoding="utf-8"))
            staffing["require_staffing_approval"] = True
            staffing_path.write_text(json.dumps(staffing), encoding="utf-8")

            pending = self.run_command("add_senior", {"root_path": str(project_root)})
            self.assertTrue(pending["ok"], pending)
            approval_id = pending["result"]["pending_approval"]["approval_id"]
            self.assertEqual(pending["result"]["slot"]["slot_id"], "senior-02")
            self.assertFalse((project_root / "state" / "slots" / "senior-02.json").exists())

            approved = self.run_command(
                "approve_checkpoint",
                {
                    "root_path": str(project_root),
                    "approval_id": approval_id,
                    "decision_summary": "Add the second senior.",
                },
            )
            self.assertTrue(approved["ok"], approved)
            self.assertTrue(approved["result"]["applied"], approved)
            self.assertEqual(approved["result"]["affected_slot_id"], "senior-02")
            self.assertTrue((project_root / "state" / "slots" / "senior-02.json").exists())
            topology = json.loads((project_root / "state" / "topology" / "current.json").read_text(encoding="utf-8"))
            self.assertIn("senior-02", topology["active_slot_ids"])

    def test_failed_staffing_approval_replay_leaves_approval_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            staffing_path = project_root / "state" / "policies" / "staffing.json"
            staffing = json.loads(staffing_path.read_text(encoding="utf-8"))
            staffing["require_staffing_approval"] = True
            staffing_path.write_text(json.dumps(staffing), encoding="utf-8")

            pending = self.run_command("retire_senior", {"root_path": str(project_root), "slot_id": "senior-01"})
            self.assertTrue(pending["ok"], pending)
            approval_id = pending["result"]["pending_approval"]["approval_id"]

            assigned = self.assign_task(project_root)
            self.assertTrue(assigned["admitted"], assigned)

            rejected_apply = self.run_command(
                "approve_checkpoint",
                {
                    "root_path": str(project_root),
                    "approval_id": approval_id,
                    "decision_summary": "Retire after approval.",
                },
                check=False,
            )
            self.assertFalse(rejected_apply["ok"], rejected_apply)
            self.assertEqual(rejected_apply["error"]["code"], "slot_has_work")
            approval = json.loads((project_root / "state" / "approvals" / f"{approval_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(approval["status"], "pending")
            self.assertIsNone(approval["decided_at"])

    def test_pending_staffing_adds_reserve_distinct_ids_and_revalidate_caps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            staffing_path = project_root / "state" / "policies" / "staffing.json"
            staffing = json.loads(staffing_path.read_text(encoding="utf-8"))
            staffing["require_staffing_approval"] = True
            staffing_path.write_text(json.dumps(staffing), encoding="utf-8")

            first = self.run_command("add_senior", {"root_path": str(project_root)})
            second = self.run_command("add_senior", {"root_path": str(project_root)})
            third = self.run_command("add_senior", {"root_path": str(project_root)}, check=False)
            self.assertTrue(first["ok"], first)
            self.assertTrue(second["ok"], second)
            self.assertFalse(third["ok"], third)
            self.assertEqual(first["result"]["slot"]["slot_id"], "senior-02")
            self.assertEqual(second["result"]["slot"]["slot_id"], "senior-03")
            self.assertEqual(third["error"]["code"], "staffing_cap_exceeded")

            for approval_id in [
                first["result"]["pending_approval"]["approval_id"],
                second["result"]["pending_approval"]["approval_id"],
            ]:
                approved = self.run_command(
                    "approve_checkpoint",
                    {
                        "root_path": str(project_root),
                        "approval_id": approval_id,
                        "decision_summary": "Add approved senior.",
                    },
                )
                self.assertTrue(approved["ok"], approved)

            topology = json.loads((project_root / "state" / "topology" / "current.json").read_text(encoding="utf-8"))
            self.assertEqual(topology["active_slot_ids"].count("senior-02"), 1)
            self.assertEqual(topology["active_slot_ids"].count("senior-03"), 1)

    def test_status_report_indexes_budget_warnings_and_rebuilds_slot_views(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            assigned = self.assign_task(project_root)
            activation_id = assigned["launch_request"]["activation_id"]
            task_id = assigned["launch_request"]["task_id"]
            task_path = project_root / "state" / "tasks" / f"{task_id}.json"
            task = json.loads(task_path.read_text(encoding="utf-8"))
            task["budget_envelope"] = {"token_budget": 100}
            task_path.write_text(json.dumps(task), encoding="utf-8")

            self.run_activation("mark-running", project_root, activation_id)
            heartbeat = self.run_activation("heartbeat", project_root, activation_id, {"token_budget": 110})
            self.assertTrue(heartbeat["ok"], heartbeat)
            self.assertTrue(heartbeat["result"]["hard_stop_required"])

            status = self.run_command("request_status", {"root_path": str(project_root)})
            self.assertTrue(status["ok"], status)
            result = status["result"]
            self.assertEqual(result["budget"]["hard_stop_activation_ids"], [activation_id])
            self.assertEqual(result["report_path"], "shared/reports/status-latest.md")
            self.assertTrue((project_root / "shared" / "reports" / "status-latest.md").exists())
            self.assertTrue((project_root / "agents" / "senior-01" / "inbox" / "index.md").exists())
            self.assertTrue((project_root / "agents" / "senior-01" / "outbox" / "index.md").exists())
            report_text = (project_root / "shared" / "reports" / "status-latest.md").read_text(encoding="utf-8")
            self.assertIn("budget_hard_limit", report_text)
            self.assertTrue(self.artifact_ids_for_path(project_root, "shared/reports/status-latest.md"))

    def test_final_package_report_is_deferred_and_replayed_through_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            deferred = self.run_command(
                "generate_report",
                {
                    "root_path": str(project_root),
                    "report_type": "final_package",
                    "scope_type": "project",
                },
            )
            self.assertTrue(deferred["ok"], deferred)
            self.assertFalse(deferred["result"]["generated"], deferred)
            approval_id = deferred["result"]["pending_approval"]["approval_id"]
            self.assertFalse((project_root / "shared" / "reports" / "final-package-latest.md").exists())

            approved = self.run_command(
                "approve_checkpoint",
                {
                    "root_path": str(project_root),
                    "approval_id": approval_id,
                    "decision_summary": "Generate final package.",
                },
            )
            self.assertTrue(approved["ok"], approved)
            self.assertTrue(approved["result"]["applied"], approved)
            self.assertIsNotNone(approved["result"]["generated_artifact"])
            self.assertTrue((project_root / "shared" / "reports" / "final-package-latest.md").exists())
            final_text = (project_root / "shared" / "reports" / "final-package-latest.md").read_text(encoding="utf-8")
            self.assertIn("# Final Package", final_text)


if __name__ == "__main__":
    unittest.main()
