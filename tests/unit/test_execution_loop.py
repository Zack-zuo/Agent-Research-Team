import json
import tempfile
import unittest
from pathlib import Path

from research_agent_team.application.approval_service import approve_checkpoint
from research_agent_team.application.experiment_service import run_experiment
from research_agent_team.application.project_service import create_project
from research_agent_team.application.task_service import assign_task
from research_agent_team.application.topology_service import add_junior
from research_agent_team.runtime.execution_loop import (
    attach_subagent,
    cancel_running_activation,
    inspect_running_activations,
    plan_pending_launches,
    reconcile_activations,
    start_pending_launches,
)
from research_agent_team.runtime.worker_launch import CodexSubagentLaunchAdapter, FakeSubagentLaunchAdapter, WorkerLaunchResult


class ReentrantLaunchAdapter:
    name = "reentrant_test"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.inner_result = None

    def launch(self, spec) -> WorkerLaunchResult:
        self.inner_result = start_pending_launches(
            str(self.root),
            adapter=FakeSubagentLaunchAdapter(launch_status="spawned"),
            max_concurrent=1,
        )
        return WorkerLaunchResult(status="spawned", handle=f"outer-{spec.activation_id}")

    def observe(self, worker_state):
        raise AssertionError("observe should not be called in this test")

    def cancel(self, worker_state, reason):
        raise AssertionError("cancel should not be called in this test")


class ExecutionLoopTests(unittest.TestCase):
    def create_project(self, root: Path) -> None:
        create_project(
            {
                "name": "Execution Loop Demo",
                "root_path": str(root),
                "initial_charter_text": "Exercise managed Codex subagent execution.",
            }
        )

    def assign_simple_task(self, root: Path, owner_slot_id: str = "senior-01", title: str = "Review notes") -> dict:
        return assign_task(
            {
                "root_path": str(root),
                "requester_slot_id": "supervisor",
                "owner_slot_id": owner_slot_id,
                "title": title,
                "description": "Read shared/raw and produce a concise summary.",
                "success_criteria": ["Write a summary"],
                "input_artifact_ids": [],
                "input_path_roots": ["shared/raw"],
                "expected_output_types": ["markdown"],
            }
        )

    def read_json(self, root: Path, relative_path: str) -> dict:
        return json.loads((root / relative_path).read_text(encoding="utf-8"))

    def test_start_pending_returns_subagent_request_and_stores_prompt_and_worker_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            assigned = self.assign_simple_task(root)
            activation_id = assigned["launch_request"]["activation_id"]

            started = start_pending_launches(
                str(root),
                adapter=FakeSubagentLaunchAdapter(launch_status="spawned"),
                max_concurrent=1,
            )

            self.assertEqual(started["started_count"], 1)
            request = started["subagent_launch_requests"][0]
            self.assertEqual(request["activation_id"], activation_id)
            self.assertEqual(request["slot_id"], "senior-01")
            self.assertIn("Before doing work, mark the activation running", request["prompt"])

            prompt_path = root / request["prompt_path"]
            worker_path = root / "agents" / "senior-01" / "activations" / activation_id / "worker.json"
            events_path = root / "agents" / "senior-01" / "activations" / activation_id / "worker-events.jsonl"
            worker = json.loads(worker_path.read_text(encoding="utf-8"))

            self.assertTrue(prompt_path.exists())
            self.assertIn(activation_id, prompt_path.read_text(encoding="utf-8"))
            self.assertTrue(events_path.exists())
            self.assertEqual(worker["status"], "spawned")
            self.assertEqual(worker["adapter"], "fake_subagent")
            self.assertEqual(worker["handle"], f"fake-{activation_id}")
            self.assertEqual(worker["prompt_path"], request["prompt_path"])

    def test_reconcile_fake_success_completes_activation_and_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            assigned = self.assign_simple_task(root)
            activation_id = assigned["launch_request"]["activation_id"]
            task_id = assigned["launch_request"]["task_id"]
            adapter = FakeSubagentLaunchAdapter(launch_status="running", observe_status="completed")

            start_pending_launches(str(root), adapter=adapter)
            reconciled = reconcile_activations(str(root), adapter=adapter)

            activation = self.read_json(root, f"state/activations/{activation_id}.json")
            task = self.read_json(root, f"state/tasks/{task_id}.json")
            worker = self.read_json(root, f"agents/senior-01/activations/{activation_id}/worker.json")
            self.assertEqual(reconciled["completed_count"], 1)
            self.assertEqual(activation["status"], "completed")
            self.assertEqual(task["status"], "completed")
            self.assertEqual(worker["status"], "completed")

    def test_reconcile_fake_failure_marks_activation_and_task_failed_with_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            assigned = self.assign_simple_task(root)
            activation_id = assigned["launch_request"]["activation_id"]
            task_id = assigned["launch_request"]["task_id"]
            adapter = FakeSubagentLaunchAdapter(
                launch_status="running",
                observe_status="failed",
                failure_summary="Fake subagent could not find the input notes.",
            )

            start_pending_launches(str(root), adapter=adapter)
            reconciled = reconcile_activations(str(root), adapter=adapter)

            activation = self.read_json(root, f"state/activations/{activation_id}.json")
            task = self.read_json(root, f"state/tasks/{task_id}.json")
            worker = self.read_json(root, f"agents/senior-01/activations/{activation_id}/worker.json")
            self.assertEqual(reconciled["failed_count"], 1)
            self.assertEqual(activation["status"], "failed")
            self.assertEqual(activation["failure_summary"], "Fake subagent could not find the input notes.")
            self.assertEqual(task["status"], "failed")
            self.assertEqual(worker["status"], "failed")
            self.assertEqual(worker["diagnostics"]["failure_summary"], "Fake subagent could not find the input notes.")

    def test_cancel_running_activation_cancels_worker_activation_and_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            assigned = self.assign_simple_task(root)
            activation_id = assigned["launch_request"]["activation_id"]
            task_id = assigned["launch_request"]["task_id"]
            adapter = FakeSubagentLaunchAdapter(launch_status="running", cancel_status="cancelled")

            start_pending_launches(str(root), adapter=adapter)
            cancelled = cancel_running_activation(str(root), activation_id, "operator_cancelled", adapter=adapter)

            activation = self.read_json(root, f"state/activations/{activation_id}.json")
            task = self.read_json(root, f"state/tasks/{task_id}.json")
            worker = self.read_json(root, f"agents/senior-01/activations/{activation_id}/worker.json")
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(activation["status"], "cancelled")
            self.assertEqual(task["status"], "cancelled")
            self.assertEqual(worker["status"], "cancelled")
            self.assertEqual(worker["cancel_reason"], "operator_cancelled")

    def test_reconcile_stale_running_activation_updates_worker_and_requeues_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            assigned = self.assign_simple_task(root)
            activation_id = assigned["launch_request"]["activation_id"]
            task_id = assigned["launch_request"]["task_id"]
            adapter = FakeSubagentLaunchAdapter(launch_status="running", observe_status="running")
            start_pending_launches(str(root), adapter=adapter)

            activation_path = root / "state" / "activations" / f"{activation_id}.json"
            activation = json.loads(activation_path.read_text(encoding="utf-8"))
            activation["lease_heartbeat_at"] = "2000-01-01T00:00:00Z"
            activation["lease_timeout_seconds"] = 1
            activation_path.write_text(json.dumps(activation), encoding="utf-8")

            reconciled = reconcile_activations(str(root), adapter=adapter)

            recovered_activation = self.read_json(root, f"state/activations/{activation_id}.json")
            task = self.read_json(root, f"state/tasks/{task_id}.json")
            worker = self.read_json(root, f"agents/senior-01/activations/{activation_id}/worker.json")
            self.assertEqual(reconciled["recovery"]["recovered_activation_count"], 1)
            self.assertEqual(recovered_activation["status"], "interrupted")
            self.assertEqual(task["status"], "queued")
            self.assertEqual(worker["status"], "stale")
            self.assertEqual(worker["diagnostics"]["reason"], "stale_heartbeat")

    def test_max_concurrency_limits_started_subagent_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            add_junior({"root_path": str(root), "parent_slot_id": "senior-01"})
            first = self.assign_simple_task(root, owner_slot_id="senior-01", title="Senior task")
            second = self.assign_simple_task(root, owner_slot_id="junior-01", title="Junior task")

            started = start_pending_launches(
                str(root),
                adapter=FakeSubagentLaunchAdapter(launch_status="spawned"),
                max_concurrent=1,
            )

            self.assertEqual(started["started_count"], 1)
            self.assertEqual(started["capacity_remaining"], 0)
            started_ids = {request["activation_id"] for request in started["subagent_launch_requests"]}
            self.assertEqual(len(started_ids), 1)
            self.assertTrue({first["launch_request"]["activation_id"], second["launch_request"]["activation_id"]} >= started_ids)

            inspected = inspect_running_activations(str(root))
            self.assertEqual(inspected["worker_count"], 1)

    def test_attach_subagent_records_host_handle_for_codex_spawn_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            assigned = self.assign_simple_task(root)
            activation_id = assigned["launch_request"]["activation_id"]

            started = start_pending_launches(str(root), adapter=CodexSubagentLaunchAdapter(), max_concurrent=1)
            self.assertEqual(started["started_count"], 1)
            self.assertIsNone(started["subagent_launch_requests"][0]["handle"])

            attached = attach_subagent(str(root), activation_id, "codex-session-123")

            worker = self.read_json(root, f"agents/senior-01/activations/{activation_id}/worker.json")
            self.assertEqual(attached["handle"], "codex-session-123")
            self.assertEqual(attached["status"], "spawned")
            self.assertEqual(worker["handle"], "codex-session-123")
            self.assertEqual(worker["status"], "spawned")

    def test_start_pending_does_not_start_experiment_confirmation_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            add_junior({"root_path": str(root), "parent_slot_id": "senior-01"})
            experiment = run_experiment(
                {
                    "root_path": str(root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "title": "Baseline experiment",
                    "objective": "Measure baseline behavior.",
                    "hypothesis": "Baseline is repeatable.",
                    "method": "Run the local-file adapter.",
                    "success_criteria": ["Publish evidence"],
                    "input_artifact_ids": [],
                    "input_path_roots": ["shared/raw"],
                    "expected_output_types": ["json", "markdown"],
                    "run_parameters": {"variant": "baseline"},
                }
            )
            activation_id = experiment["launch_request"]["activation_id"]

            planned = plan_pending_launches(str(root))
            started = start_pending_launches(str(root), adapter=FakeSubagentLaunchAdapter(launch_status="spawned"))

            self.assertEqual(planned["launch_plan"]["confirm_launch_count"], 1)
            self.assertEqual(started["started_count"], 0)
            self.assertEqual(started["blocked_count"], 1)
            self.assertEqual(started["blocked_launches"][0]["activation_id"], activation_id)
            self.assertFalse((root / "agents" / "junior-01" / "activations" / activation_id / "worker.json").exists())

    def test_pending_plan_preserves_approval_replay_confirmation_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            pending = assign_task(
                {
                    "root_path": str(root),
                    "requester_slot_id": "supervisor",
                    "owner_slot_id": "senior-01",
                    "title": "Budget gated task",
                    "description": "Needs approval before admission.",
                    "success_criteria": ["Write notes"],
                    "budget_override": {"token_budget": 1000},
                }
            )
            approve_checkpoint(
                {
                    "root_path": str(root),
                    "approval_id": pending["pending_approval"]["approval_id"],
                    "decision_summary": "Approved for test.",
                }
            )

            planned = plan_pending_launches(str(root))
            started = start_pending_launches(str(root), adapter=FakeSubagentLaunchAdapter(launch_status="spawned"))

            self.assertEqual(planned["launch_plan"]["auto_launch_count"], 0)
            self.assertEqual(planned["launch_plan"]["confirm_launch_count"], 1)
            self.assertIn("approval replay", planned["launch_plan"]["decisions"][0]["reason"])
            self.assertEqual(started["started_count"], 0)
            self.assertEqual(started["blocked_count"], 1)

    def test_start_pending_reserves_worker_before_adapter_launch_to_prevent_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            assigned = self.assign_simple_task(root)
            activation_id = assigned["launch_request"]["activation_id"]
            adapter = ReentrantLaunchAdapter(root)

            started = start_pending_launches(str(root), adapter=adapter, max_concurrent=1)

            self.assertEqual(started["started_count"], 1)
            self.assertIsNotNone(adapter.inner_result)
            self.assertEqual(adapter.inner_result["started_count"], 0)
            self.assertEqual(adapter.inner_result["active_worker_count"], 1)
            worker = self.read_json(root, f"agents/senior-01/activations/{activation_id}/worker.json")
            self.assertEqual(worker["handle"], f"outer-{activation_id}")


if __name__ == "__main__":
    unittest.main()
