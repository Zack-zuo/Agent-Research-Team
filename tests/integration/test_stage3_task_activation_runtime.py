import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "research-agent-team"
SCRIPT = PLUGIN_ROOT / "scripts" / "rat_plugin_cli.py"


class Stage3TaskActivationRuntimeTests(unittest.TestCase):
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

    def run_activation(self, command_name: str, root_path: Path, activation_id: str, payload: dict = None) -> dict:
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
        result = self.run_cli(*args)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"invalid JSON output: {exc}\nstdout={result.stdout}\nstderr={result.stderr}")

    def create_project(self, project_root: Path) -> None:
        payload = self.run_command(
            "create_project",
            {
                "name": "Stage 3 Demo",
                "root_path": str(project_root),
                "initial_charter_text": "Investigate task admission.",
            },
        )
        self.assertTrue(payload["ok"], payload)

    def assign_task(self, project_root: Path, title: str = "Review notes", owner: str = "senior-01") -> dict:
        payload = self.run_command(
            "assign_task",
            {
                "root_path": str(project_root),
                "requester_slot_id": "supervisor",
                "owner_slot_id": owner,
                "title": title,
                "description": "Read the shared notes and produce a concise summary.",
                "success_criteria": ["Write a summary", "List next actions"],
                "input_artifact_ids": [],
                "input_path_roots": ["shared/raw"],
                "expected_output_types": ["markdown"],
            },
        )
        self.assertTrue(payload["ok"], payload)
        return payload["result"]

    def test_assign_task_admits_idle_slot_and_materializes_activation_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            result = self.assign_task(project_root)

            self.assertTrue(result["admitted"], result)
            self.assertEqual(result["owner_queue_depth"], 0)
            launch = result["launch_request"]
            self.assertIsNotNone(launch)
            activation_id = launch["activation_id"]
            task_id = launch["task_id"]

            for relative_path in [launch["bundle_path"], launch["briefing_path"], launch["runtime_metadata_path"]]:
                self.assertTrue((project_root / relative_path).exists(), relative_path)

            task = json.loads((project_root / "state" / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
            activation = json.loads(
                (project_root / "state" / "activations" / f"{activation_id}.json").read_text(encoding="utf-8")
            )
            slot = json.loads((project_root / "state" / "slots" / "senior-01.json").read_text(encoding="utf-8"))
            bundle = json.loads((project_root / launch["bundle_path"]).read_text(encoding="utf-8"))

            self.assertEqual(task["status"], "admitted")
            self.assertEqual(task["current_activation_id"], activation_id)
            self.assertEqual(activation["status"], "starting")
            self.assertEqual(slot["current_activation_id"], activation_id)
            self.assertEqual(slot["queued_task_ids"], [])
            self.assertEqual(slot["active_task_ids"], [task_id])
            self.assertEqual(bundle["task_id"], task_id)
            self.assertEqual(bundle["allowed_path_roots"], ["shared/raw"])

            prompt = self.run_cli(
                "render-launch-prompt",
                "--root-path",
                str(project_root),
                "--payload-json",
                json.dumps(launch),
            )
            self.assertIn("senior PhD activation", prompt.stdout)
            self.assertIn("activation mark-running", prompt.stdout)
            self.assertIn(activation_id, prompt.stdout)

    def test_assign_task_accepts_schema_minimal_payload_without_success_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            payload = self.run_command(
                "assign_task",
                {
                    "root_path": str(project_root),
                    "requester_slot_id": "supervisor",
                    "owner_slot_id": "senior-01",
                    "title": "Schema minimal task",
                    "description": "Use only fields required by the public command schema.",
                },
            )

            self.assertTrue(payload["ok"], payload)
            result = payload["result"]
            self.assertTrue(result["admitted"], result)
            bundle = json.loads((project_root / result["launch_request"]["bundle_path"]).read_text(encoding="utf-8"))
            self.assertEqual(bundle["success_criteria"], [])

    def test_budget_override_task_blocks_slot_retirement_while_awaiting_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            assigned = self.run_command(
                "assign_task",
                {
                    "root_path": str(project_root),
                    "requester_slot_id": "supervisor",
                    "owner_slot_id": "senior-01",
                    "title": "Approval gated task",
                    "description": "Request a smaller explicit budget that requires approval.",
                    "success_criteria": ["Create an approval checkpoint"],
                    "budget_override": {"token_budget": 1000},
                },
            )

            self.assertTrue(assigned["ok"], assigned)
            result = assigned["result"]
            self.assertFalse(result["admitted"], result)
            self.assertIsNotNone(result["pending_approval"])
            task_id = result["task"]["task_id"]
            task = json.loads((project_root / "state" / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
            slot = json.loads((project_root / "state" / "slots" / "senior-01.json").read_text(encoding="utf-8"))
            self.assertEqual(task["status"], "awaiting_approval")
            self.assertIn(task_id, slot["active_task_ids"])
            self.assertEqual(slot["queued_task_ids"], [])

            retired = self.run_command(
                "retire_senior",
                {"root_path": str(project_root), "slot_id": "senior-01"},
                check=False,
            )
            self.assertFalse(retired["ok"], retired)
            self.assertEqual(retired["error"]["code"], "slot_has_work")

    def test_busy_and_paused_slots_preserve_queued_work_without_duplicate_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            first = self.assign_task(project_root, title="First task")
            self.assertTrue(first["admitted"], first)
            first_activation_id = first["launch_request"]["activation_id"]

            second = self.assign_task(project_root, title="Second task")
            self.assertFalse(second["admitted"], second)
            self.assertIsNone(second["launch_request"])
            self.assertEqual(second["owner_queue_depth"], 1)

            paused = self.run_command("pause_project", {"root_path": str(project_root)})
            self.assertTrue(paused["ok"], paused)
            third = self.assign_task(project_root, title="Third task")
            self.assertFalse(third["admitted"], third)
            self.assertIsNone(third["launch_request"])
            self.assertEqual(third["owner_queue_depth"], 2)

            slot = json.loads((project_root / "state" / "slots" / "senior-01.json").read_text(encoding="utf-8"))
            self.assertEqual(slot["current_activation_id"], first_activation_id)
            self.assertEqual(len(slot["queued_task_ids"]), 2)

    def test_activation_callbacks_run_checkpoint_complete_and_advance_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            first = self.assign_task(project_root, title="First task")
            second = self.assign_task(project_root, title="Second task")
            self.assertFalse(second["admitted"], second)
            activation_id = first["launch_request"]["activation_id"]
            task_id = first["launch_request"]["task_id"]

            running = self.run_activation("mark-running", project_root, activation_id)
            self.assertTrue(running["ok"], running)
            task = json.loads((project_root / "state" / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(task["status"], "running")

            heartbeat = self.run_activation("heartbeat", project_root, activation_id, {"token_budget": 25})
            self.assertTrue(heartbeat["ok"], heartbeat)
            self.assertFalse(heartbeat["result"]["hard_stop_required"])

            checkpoint = self.run_activation(
                "checkpoint",
                project_root,
                activation_id,
                {"summary": "Draft complete.", "resume_instructions": "Continue from the draft."},
            )
            self.assertTrue(checkpoint["ok"], checkpoint)
            checkpoint_id = checkpoint["result"]["checkpoint_id"]
            self.assertTrue((project_root / "state" / "checkpoints" / "senior-01.json").exists())
            self.assertTrue((project_root / "agents" / "senior-01" / "checkpoints" / checkpoint_id / "summary.md").exists())

            completed = self.run_activation("complete", project_root, activation_id, {"output_artifact_ids": []})
            self.assertTrue(completed["ok"], completed)

            completed_task = json.loads((project_root / "state" / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
            slot = json.loads((project_root / "state" / "slots" / "senior-01.json").read_text(encoding="utf-8"))
            self.assertEqual(completed_task["status"], "completed")
            self.assertIn(task_id, slot["completed_task_ids"])
            self.assertEqual(slot["queued_task_ids"], [])
            self.assertIsNotNone(slot["current_activation_id"])

            next_activation = json.loads(
                (project_root / "state" / "activations" / f"{slot['current_activation_id']}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(next_activation["status"], "starting")

    def test_open_project_recovers_stale_running_activation_by_requeueing_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            assigned = self.assign_task(project_root)
            activation_id = assigned["launch_request"]["activation_id"]
            task_id = assigned["launch_request"]["task_id"]
            self.run_activation("mark-running", project_root, activation_id)

            activation_path = project_root / "state" / "activations" / f"{activation_id}.json"
            activation = json.loads(activation_path.read_text(encoding="utf-8"))
            activation["lease_heartbeat_at"] = "2000-01-01T00:00:00Z"
            activation["lease_timeout_seconds"] = 1
            activation_path.write_text(json.dumps(activation), encoding="utf-8")

            opened = self.run_command("open_project", {"root_path": str(project_root)})
            self.assertTrue(opened["ok"], opened)
            self.assertEqual(opened["result"]["recovery"]["recovered_activation_count"], 1)

            recovered_activation = json.loads(activation_path.read_text(encoding="utf-8"))
            task = json.loads((project_root / "state" / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
            slot = json.loads((project_root / "state" / "slots" / "senior-01.json").read_text(encoding="utf-8"))

            self.assertEqual(recovered_activation["status"], "interrupted")
            self.assertEqual(task["status"], "queued")
            self.assertIsNone(slot["current_activation_id"])
            self.assertEqual(slot["queued_task_ids"], [task_id])

    def test_open_project_recovers_stale_starting_activation_by_requeueing_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            assigned = self.assign_task(project_root)
            activation_id = assigned["launch_request"]["activation_id"]
            task_id = assigned["launch_request"]["task_id"]

            activation_path = project_root / "state" / "activations" / f"{activation_id}.json"
            activation = json.loads(activation_path.read_text(encoding="utf-8"))
            self.assertEqual(activation["status"], "starting")
            activation["lease_heartbeat_at"] = "2000-01-01T00:00:00Z"
            activation["lease_timeout_seconds"] = 1
            activation_path.write_text(json.dumps(activation), encoding="utf-8")

            opened = self.run_command("open_project", {"root_path": str(project_root)})
            self.assertTrue(opened["ok"], opened)
            self.assertEqual(opened["result"]["recovery"]["recovered_activation_count"], 1)

            recovered_activation = json.loads(activation_path.read_text(encoding="utf-8"))
            task = json.loads((project_root / "state" / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
            slot = json.loads((project_root / "state" / "slots" / "senior-01.json").read_text(encoding="utf-8"))

            self.assertEqual(recovered_activation["status"], "interrupted")
            self.assertEqual(recovered_activation["pending_stop_reason"], "stale_heartbeat")
            self.assertEqual(task["status"], "queued")
            self.assertIsNone(slot["current_activation_id"])
            self.assertEqual(slot["queued_task_ids"], [task_id])

    def test_failure_interruption_and_cancellation_callbacks_persist_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            failed_root = Path(tmpdir) / "failed-project"
            self.create_project(failed_root)
            failed = self.assign_task(failed_root, title="Failing task")
            failed_activation_id = failed["launch_request"]["activation_id"]
            failed_task_id = failed["launch_request"]["task_id"]
            self.run_activation("mark-running", failed_root, failed_activation_id)

            failed_result = self.run_activation(
                "fail",
                failed_root,
                failed_activation_id,
                {"failure_summary": "The input evidence was insufficient."},
            )
            self.assertTrue(failed_result["ok"], failed_result)
            failed_task = json.loads((failed_root / "state" / "tasks" / f"{failed_task_id}.json").read_text(encoding="utf-8"))
            failed_activation = json.loads(
                (failed_root / "state" / "activations" / f"{failed_activation_id}.json").read_text(encoding="utf-8")
            )
            failed_slot = json.loads((failed_root / "state" / "slots" / "senior-01.json").read_text(encoding="utf-8"))
            self.assertEqual(failed_task["status"], "failed")
            self.assertEqual(failed_activation["status"], "failed")
            self.assertIsNone(failed_slot["current_activation_id"])

            interrupted_root = Path(tmpdir) / "interrupted-project"
            self.create_project(interrupted_root)
            interrupted = self.assign_task(interrupted_root, title="Interrupted task")
            interrupted_activation_id = interrupted["launch_request"]["activation_id"]
            interrupted_task_id = interrupted["launch_request"]["task_id"]
            self.run_activation("mark-running", interrupted_root, interrupted_activation_id)

            interrupted_result = self.run_activation("interrupt", interrupted_root, interrupted_activation_id, {"reason": "user_pause"})
            self.assertTrue(interrupted_result["ok"], interrupted_result)
            interrupted_task = json.loads(
                (interrupted_root / "state" / "tasks" / f"{interrupted_task_id}.json").read_text(encoding="utf-8")
            )
            interrupted_activation = json.loads(
                (interrupted_root / "state" / "activations" / f"{interrupted_activation_id}.json").read_text(encoding="utf-8")
            )
            interrupted_slot = json.loads((interrupted_root / "state" / "slots" / "senior-01.json").read_text(encoding="utf-8"))
            self.assertEqual(interrupted_task["status"], "queued")
            self.assertEqual(interrupted_activation["status"], "interrupted")
            self.assertEqual(interrupted_slot["queued_task_ids"], [interrupted_task_id])

            cancelled_root = Path(tmpdir) / "cancelled-project"
            self.create_project(cancelled_root)
            cancelled = self.assign_task(cancelled_root, title="Cancelled task")
            cancelled_activation_id = cancelled["launch_request"]["activation_id"]
            cancelled_task_id = cancelled["launch_request"]["task_id"]

            cancelled_result = self.run_activation("cancel", cancelled_root, cancelled_activation_id, {"reason": "no_longer_needed"})
            self.assertTrue(cancelled_result["ok"], cancelled_result)
            cancelled_task = json.loads(
                (cancelled_root / "state" / "tasks" / f"{cancelled_task_id}.json").read_text(encoding="utf-8")
            )
            cancelled_activation = json.loads(
                (cancelled_root / "state" / "activations" / f"{cancelled_activation_id}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(cancelled_task["status"], "cancelled")
            self.assertEqual(cancelled_activation["status"], "cancelled")

    def test_budget_thresholds_use_cumulative_task_usage_across_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            assigned = self.assign_task(project_root)
            first_activation_id = assigned["launch_request"]["activation_id"]
            task_id = assigned["launch_request"]["task_id"]
            task_path = project_root / "state" / "tasks" / f"{task_id}.json"
            task = json.loads(task_path.read_text(encoding="utf-8"))
            task["budget_envelope"] = {"token_budget": 100}
            task_path.write_text(json.dumps(task), encoding="utf-8")

            self.run_activation("mark-running", project_root, first_activation_id)
            first_heartbeat = self.run_activation("heartbeat", project_root, first_activation_id, {"token_budget": 60})
            self.assertTrue(first_heartbeat["ok"], first_heartbeat)
            self.assertFalse(first_heartbeat["result"]["hard_stop_required"])

            interrupted = self.run_activation("interrupt", project_root, first_activation_id, {"reason": "retry_budget"})
            self.assertTrue(interrupted["ok"], interrupted)
            resumed = self.run_command("resume_project", {"root_path": str(project_root)})
            self.assertTrue(resumed["ok"], resumed)
            second_activation_id = resumed["result"]["launch_requests"][0]["activation_id"]

            self.run_activation("mark-running", project_root, second_activation_id)
            second_heartbeat = self.run_activation("heartbeat", project_root, second_activation_id, {"token_budget": 50})
            self.assertTrue(second_heartbeat["ok"], second_heartbeat)
            self.assertTrue(second_heartbeat["result"]["hard_stop_required"])
            self.assertEqual(second_heartbeat["result"]["pending_stop_reason"], "budget_hard_limit")

            activation = json.loads(
                (project_root / "state" / "activations" / f"{second_activation_id}.json").read_text(encoding="utf-8")
            )
            budget = json.loads((project_root / "state" / "budgets" / "current.json").read_text(encoding="utf-8"))
            self.assertEqual(activation["consumed_budget"]["token_budget"], 50.0)
            self.assertEqual(budget["consumed_to_date"]["task"][task_id]["token_budget"], 110.0)

    def test_render_launch_prompt_rejects_project_root_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            result = self.run_cli(
                "render-launch-prompt",
                "--root-path",
                str(project_root),
                "--payload-json",
                json.dumps(
                    {
                        "activation_id": "activation-bad",
                        "slot_id": "senior-01",
                        "task_id": "task-bad",
                        "bundle_path": "../bundle.json",
                        "briefing_path": "agents/senior-01/activations/a/briefing.md",
                        "runtime_metadata_path": "agents/senior-01/activations/a/runtime.json",
                    }
                ),
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("escapes project root", result.stderr)

    def test_render_launch_prompt_rejects_non_starting_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            assigned = self.assign_task(project_root)
            launch = assigned["launch_request"]
            activation_id = launch["activation_id"]
            self.run_activation("mark-running", project_root, activation_id)

            result = self.run_cli(
                "render-launch-prompt",
                "--root-path",
                str(project_root),
                "--payload-json",
                json.dumps(launch),
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must still be starting", result.stderr)


if __name__ == "__main__":
    unittest.main()
