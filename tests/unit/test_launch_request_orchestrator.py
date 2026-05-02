import tempfile
import unittest
from pathlib import Path

from research_agent_team.application.activation_service import mark_activation_running
from research_agent_team.application.approval_service import approve_checkpoint
from research_agent_team.application.project_service import create_project
from research_agent_team.application.task_service import assign_task
from research_agent_team.application.topology_service import add_junior
from research_agent_team.application.experiment_service import run_experiment
from research_agent_team.runtime.launch_request_orchestrator import collect_launch_requests, plan_launches


class LaunchRequestOrchestratorTests(unittest.TestCase):
    def create_project(self, root: Path) -> None:
        create_project(
            {
                "name": "Launch Orchestrator Demo",
                "root_path": str(root),
                "initial_charter_text": "Test launch request orchestration.",
            }
        )

    def assign_simple_task(self, root: Path, title: str = "Review notes") -> dict:
        return assign_task(
            {
                "root_path": str(root),
                "requester_slot_id": "supervisor",
                "owner_slot_id": "senior-01",
                "title": title,
                "description": "Read shared/raw and write concise notes.",
                "success_criteria": ["Write notes"],
                "input_artifact_ids": [],
                "input_path_roots": ["shared/raw"],
                "expected_output_types": ["markdown"],
            }
        )

    def test_collect_launch_requests_finds_known_result_fields(self) -> None:
        launch_a = {
            "activation_id": "activation-a",
            "slot_id": "senior-01",
            "task_id": "task-a",
            "bundle_path": "agents/senior-01/activations/activation-a/bundle.json",
            "briefing_path": "agents/senior-01/activations/activation-a/briefing.md",
            "runtime_metadata_path": "agents/senior-01/activations/activation-a/runtime.json",
        }
        launch_b = {**launch_a, "activation_id": "activation-b", "task_id": "task-b"}
        launch_c = {**launch_a, "activation_id": "activation-c", "task_id": "task-c"}
        launch_d = {**launch_a, "activation_id": "activation-d", "task_id": "task-d"}

        candidates = collect_launch_requests(
            {
                "ok": True,
                "result": {
                    "launch_request": launch_a,
                    "launch_requests": [launch_b],
                    "follow_up_launch_request": launch_c,
                    "next_launch_request": launch_d,
                    "ignored_launch_request": None,
                },
            }
        )

        self.assertEqual(
            [candidate.source_path for candidate in candidates],
            [
                "result.launch_request",
                "result.launch_requests[0]",
                "result.follow_up_launch_request",
                "result.next_launch_request",
            ],
        )
        self.assertEqual([candidate.launch_request["activation_id"] for candidate in candidates], [
            "activation-a",
            "activation-b",
            "activation-c",
            "activation-d",
        ])

    def test_simple_assign_task_plans_conservative_auto_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            result = self.assign_simple_task(root)

            planned = plan_launches(str(root), {"ok": True, "result": result}, source_command="assign_task")

            self.assertEqual(planned["policy"], "conservative")
            self.assertEqual(planned["launch_request_count"], 1)
            self.assertEqual(planned["auto_launch_count"], 1)
            decision = planned["decisions"][0]
            self.assertEqual(decision["action"], "auto_launch")
            self.assertEqual(decision["reason"], "clear non-experiment activation is safe to launch automatically")
            self.assertEqual(decision["launch_request"]["activation_id"], result["launch_request"]["activation_id"])
            self.assertEqual(decision["activation"]["status"], "starting")
            self.assertEqual(decision["task"]["review_requirement"], "none")

    def test_busy_slot_without_launch_request_explains_queue_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            self.assign_simple_task(root, title="First task")
            queued = self.assign_simple_task(root, title="Second task")

            planned = plan_launches(str(root), {"ok": True, "result": queued}, source_command="assign_task")

            self.assertEqual(planned["launch_request_count"], 0)
            self.assertEqual(planned["decisions"], [])
            self.assertIn("No launch_request was returned", planned["messages"][0])
            self.assertIn("queued", planned["messages"][0])

    def test_experiment_launch_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            add_junior({"root_path": str(root), "parent_slot_id": "senior-01"})
            result = run_experiment(
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

            planned = plan_launches(str(root), {"ok": True, "result": result}, source_command="run_experiment")

            self.assertEqual(planned["confirm_launch_count"], 1)
            decision = planned["decisions"][0]
            self.assertEqual(decision["action"], "confirm_launch")
            self.assertIn("experiment", decision["reason"])
            self.assertEqual(decision["task"]["review_requirement"], "experiment_review")

    def test_approval_replay_launch_requires_confirmation(self) -> None:
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
            approval_id = pending["pending_approval"]["approval_id"]
            approved = approve_checkpoint(
                {
                    "root_path": str(root),
                    "approval_id": approval_id,
                    "decision_summary": "Approved for test.",
                }
            )

            planned = plan_launches(str(root), {"ok": True, "result": approved}, source_command="approve_checkpoint")

            self.assertEqual(planned["confirm_launch_count"], 1)
            decision = planned["decisions"][0]
            self.assertEqual(decision["action"], "confirm_launch")
            self.assertIn("approval replay", decision["reason"])

    def test_approval_replay_requires_confirmation_without_source_command(self) -> None:
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
            approved = approve_checkpoint(
                {
                    "root_path": str(root),
                    "approval_id": pending["pending_approval"]["approval_id"],
                    "decision_summary": "Approved for test.",
                }
            )

            planned = plan_launches(str(root), {"ok": True, "result": approved})

            self.assertEqual(planned["auto_launch_count"], 0)
            self.assertEqual(planned["confirm_launch_count"], 1)
            self.assertEqual(planned["decisions"][0]["action"], "confirm_launch")
            self.assertIn("approval replay", planned["decisions"][0]["reason"])

    def test_failed_command_result_blocks_nested_launch_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            result = self.assign_simple_task(root)

            planned = plan_launches(str(root), {"ok": False, "result": result}, source_command="assign_task")

            self.assertEqual(planned["launch_request_count"], 1)
            self.assertEqual(planned["auto_launch_count"], 0)
            self.assertEqual(planned["blocked_count"], 1)
            self.assertEqual(planned["decisions"][0]["action"], "blocked")
            self.assertIn("Command did not succeed", planned["decisions"][0]["reason"])
            self.assertIn("skipped", planned["messages"][0])

    def test_invalid_or_stale_launch_request_does_not_mutate_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            result = self.assign_simple_task(root)
            activation_id = result["launch_request"]["activation_id"]
            mark_activation_running(str(root), activation_id, None)

            planned = plan_launches(str(root), {"ok": True, "result": result}, source_command="assign_task")

            self.assertEqual(planned["error_count"], 1)
            decision = planned["decisions"][0]
            self.assertEqual(decision["action"], "error")
            self.assertIn("starting", decision["reason"])
            self.assertEqual(decision["activation"]["status"], "running")


if __name__ == "__main__":
    unittest.main()
