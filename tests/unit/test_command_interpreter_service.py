import tempfile
import unittest
from pathlib import Path

from research_agent_team.application.activation_service import mark_activation_running, persist_checkpoint
from research_agent_team.application.command_interpreter_service import interpret_command
from research_agent_team.application.project_service import create_project, pause_project
from research_agent_team.application.task_service import assign_task
from research_agent_team.application.topology_service import add_junior
from research_agent_team.contracts.interpretation import InterpretationContext


class CommandInterpreterServiceTests(unittest.TestCase):
    def create_project(self, root: Path) -> None:
        create_project(
            {
                "name": "Interpreter Demo",
                "root_path": str(root),
                "initial_charter_text": "Interpret natural-language commands.",
            }
        )

    def create_project_with_junior(self, root: Path) -> None:
        self.create_project(root)
        add_junior({"root_path": str(root), "parent_slot_id": "senior-01"})

    def test_open_and_status_resolve_current_project_without_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            context = InterpretationContext(root_path=str(root))

            opened = interpret_command("open this project", context)
            self.assertEqual(opened.intent, "open_project")
            self.assertEqual(opened.command_name, "open_project")
            self.assertEqual(opened.payload, {"root_path": str(root.resolve())})
            self.assertEqual(opened.missing_fields, [])
            self.assertFalse(opened.needs_confirmation)
            self.assertTrue(opened.ready_for_execution)
            self.assertGreaterEqual(opened.confidence, 0.8)

            status = interpret_command("show current progress", context)
            self.assertEqual(status.command_name, "request_status")
            self.assertFalse(status.needs_confirmation)
            self.assertTrue(status.ready_for_execution)

    def test_missing_project_context_marks_root_path_missing(self) -> None:
        interpreted = interpret_command("pause the project")

        self.assertEqual(interpreted.command_name, "pause_project")
        self.assertIn("root_path", interpreted.missing_fields)
        self.assertTrue(interpreted.needs_confirmation)
        self.assertFalse(interpreted.ready_for_execution)

    def test_context_accepts_current_project_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)

            interpreted = interpret_command("open this project", {"current_project": {"root_path": str(root)}})

            self.assertEqual(interpreted.command_name, "open_project")
            self.assertEqual(interpreted.payload["root_path"], str(root.resolve()))
            self.assertTrue(interpreted.ready_for_execution)

    def test_task_assignment_resolves_slot_path_and_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)

            interpreted = interpret_command(
                "assign senior-01 a literature review task to review shared/raw",
                InterpretationContext(root_path=str(root)),
            )

            self.assertEqual(interpreted.command_name, "assign_task")
            self.assertEqual(interpreted.payload["requester_slot_id"], "supervisor")
            self.assertEqual(interpreted.payload["owner_slot_id"], "senior-01")
            self.assertEqual(interpreted.payload["title"], "Literature review")
            self.assertIn("literature review", interpreted.payload["description"].lower())
            self.assertEqual(interpreted.payload["input_path_roots"], ["shared/raw"])
            self.assertEqual(interpreted.payload["expected_output_types"], ["markdown"])
            self.assertEqual(interpreted.missing_fields, [])
            self.assertEqual(interpreted.ambiguous_references, [])
            self.assertTrue(interpreted.needs_confirmation)
            self.assertTrue(interpreted.ready_for_execution)

    def test_task_assignment_preserves_input_path_casing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)

            interpreted = interpret_command(
                "assign senior-01 a literature review task to review shared/raw/PaperA.pdf",
                InterpretationContext(root_path=str(root)),
            )

            self.assertEqual(interpreted.command_name, "assign_task")
            self.assertEqual(interpreted.payload["input_path_roots"], ["shared/raw/PaperA.pdf"])
            self.assertTrue(interpreted.ready_for_execution)

    def test_aggressive_confirmation_mode_allows_clear_task_plan_without_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)

            interpreted = interpret_command(
                "assign senior-01 a literature review task",
                InterpretationContext(root_path=str(root), confirmation_mode="aggressive"),
            )

            self.assertEqual(interpreted.command_name, "assign_task")
            self.assertFalse(interpreted.needs_confirmation)
            self.assertTrue(interpreted.ready_for_execution)

    def test_invalid_slot_reference_blocks_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)

            interpreted = interpret_command(
                "assign senior-99 a literature review task",
                InterpretationContext(root_path=str(root)),
            )

            self.assertEqual(interpreted.command_name, "assign_task")
            self.assertIn("owner_slot_id", interpreted.validation_errors[0])
            self.assertTrue(interpreted.needs_confirmation)
            self.assertFalse(interpreted.ready_for_execution)

    def test_ambiguous_slot_reference_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            add_junior({"root_path": str(root), "parent_slot_id": "senior-01"})

            interpreted = interpret_command(
                "assign a junior a replication task",
                InterpretationContext(root_path=str(root)),
            )

            self.assertEqual(interpreted.command_name, "assign_task")
            self.assertIn("owner_slot_id", interpreted.missing_fields)
            self.assertEqual(interpreted.ambiguous_references[0]["field"], "owner_slot_id")
            self.assertEqual(interpreted.ambiguous_references[0]["candidates"], ["junior-01"])
            self.assertFalse(interpreted.ready_for_execution)

    def test_pause_resume_and_checkpoint_reference_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            assigned = assign_task(
                {
                    "root_path": str(root),
                    "requester_slot_id": "supervisor",
                    "owner_slot_id": "senior-01",
                    "title": "Review notes",
                    "description": "Review the notes.",
                }
            )
            activation_id = assigned["launch_request"]["activation_id"]
            mark_activation_running(str(root), activation_id, None)
            checkpoint = persist_checkpoint(
                str(root),
                activation_id,
                {
                    "summary": "Initial notes reviewed.",
                    "resume_instructions": "Continue from the reviewed notes.",
                },
            )
            pause_project({"root_path": str(root)})

            paused = interpret_command("pause the project", InterpretationContext(root_path=str(root)))
            self.assertEqual(paused.command_name, "pause_project")
            self.assertTrue(paused.needs_confirmation)

            resumed = interpret_command("resume from the last checkpoint", InterpretationContext(root_path=str(root)))
            self.assertEqual(resumed.command_name, "resume_project")
            self.assertTrue(resumed.needs_confirmation)
            self.assertTrue(resumed.ready_for_execution)
            self.assertIn(
                {
                    "field": "latest_checkpoint_id",
                    "value": checkpoint["checkpoint_id"],
                    "source": "project_state",
                },
                resumed.resolved_references,
            )

    def test_knowledge_graph_and_experiment_mappings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(root)
            context = InterpretationContext(root_path=str(root))

            sync = interpret_command("sync the knowledge base", context)
            self.assertEqual(sync.command_name, "sync_knowledge_base")
            self.assertEqual(sync.payload["scope_type"], "project")
            self.assertEqual(sync.payload["mode"], "incremental")
            self.assertFalse(sync.needs_confirmation)

            graph = interpret_command("rebuild the graph from scratch", context)
            self.assertEqual(graph.command_name, "rebuild_graph")
            self.assertEqual(graph.payload["mode"], "full")
            self.assertTrue(graph.needs_confirmation)

            experiment = interpret_command("run a baseline experiment", context)
            self.assertEqual(experiment.command_name, "run_experiment")
            self.assertEqual(experiment.payload["requester_slot_id"], "senior-01")
            self.assertEqual(experiment.payload["executor_slot_id"], "junior-01")
            self.assertEqual(experiment.payload["title"], "Baseline experiment")
            self.assertEqual(experiment.payload["run_parameters"], {"variant": "baseline"})
            self.assertTrue(experiment.needs_confirmation)
            self.assertTrue(experiment.ready_for_execution)

    def test_experiment_preserves_input_path_casing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(root)

            interpreted = interpret_command(
                "run a baseline experiment using shared/raw/PaperA.pdf",
                InterpretationContext(root_path=str(root)),
            )

            self.assertEqual(interpreted.command_name, "run_experiment")
            self.assertEqual(interpreted.payload["input_path_roots"], ["shared/raw/PaperA.pdf"])
            self.assertTrue(interpreted.ready_for_execution)

    def test_generate_final_package_report_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)

            interpreted = interpret_command("generate the final package report", InterpretationContext(root_path=str(root)))

            self.assertEqual(interpreted.command_name, "generate_report")
            self.assertEqual(interpreted.payload["report_type"], "final_package")
            self.assertEqual(interpreted.payload["scope_type"], "project")
            self.assertTrue(interpreted.needs_confirmation)
            self.assertTrue(interpreted.ready_for_execution)

    def test_generate_status_report_routes_to_generate_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)

            interpreted = interpret_command("generate status report", InterpretationContext(root_path=str(root)))

            self.assertEqual(interpreted.command_name, "generate_report")
            self.assertEqual(interpreted.payload["report_type"], "status")
            self.assertTrue(interpreted.ready_for_execution)

    def test_invalid_project_root_blocks_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_root = Path(tmpdir) / "not-a-project"

            interpreted = interpret_command("show current progress", InterpretationContext(root_path=str(missing_root)))

            self.assertEqual(interpreted.command_name, "request_status")
            self.assertIn("root_path does not reference an existing ResearchAgentTeam project", interpreted.validation_errors)
            self.assertTrue(interpreted.needs_confirmation)
            self.assertFalse(interpreted.ready_for_execution)

    def test_experiment_review_routes_before_run_experiment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(root)

            interpreted = interpret_command(
                "review experiment-run-123 as accepted",
                InterpretationContext(root_path=str(root), confirmation_mode="aggressive"),
            )

            self.assertEqual(interpreted.command_name, "review_experiment")
            self.assertEqual(interpreted.payload["experiment_run_id"], "experiment-run-123")
            self.assertEqual(interpreted.payload["reviewer_slot_id"], "senior-01")
            self.assertEqual(interpreted.payload["outcome"], "accepted")
            self.assertFalse(interpreted.needs_confirmation)
            self.assertTrue(interpreted.ready_for_execution)

    def test_experiment_without_junior_executor_requires_more_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)

            interpreted = interpret_command("run a baseline experiment", InterpretationContext(root_path=str(root)))

            self.assertEqual(interpreted.command_name, "run_experiment")
            self.assertIn("executor_slot_id", interpreted.missing_fields)
            self.assertTrue(interpreted.needs_confirmation)
            self.assertFalse(interpreted.ready_for_execution)


if __name__ == "__main__":
    unittest.main()
