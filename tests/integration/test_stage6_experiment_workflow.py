import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "research-agent-team"
SCRIPT = PLUGIN_ROOT / "scripts" / "rat_plugin_cli.py"


class Stage6ExperimentWorkflowTests(unittest.TestCase):
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

    def create_project_with_junior(self, project_root: Path) -> None:
        created = self.run_command(
            "create_project",
            {
                "name": "Stage 6 Demo",
                "root_path": str(project_root),
                "initial_charter_text": "Experiment workflow project.",
            },
        )
        self.assertTrue(created["ok"], created)
        junior = self.run_command("add_junior", {"root_path": str(project_root), "parent_slot_id": "senior-01"})
        self.assertTrue(junior["ok"], junior)
        self.assertEqual(junior["result"]["slot"]["slot_id"], "junior-01")

    def artifact_index(self, project_root: Path) -> list[dict]:
        index_path = project_root / "state" / "artifacts" / "index.jsonl"
        return [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def run_and_publish_experiment(self, project_root: Path, **overrides: object) -> dict:
        payload = {
            "root_path": str(project_root),
            "requester_slot_id": "senior-01",
            "executor_slot_id": "junior-01",
            "title": "Experiment",
            "objective": "Create deterministic evidence.",
            "hypothesis": "The local adapter will publish evidence.",
            "method": "Run the local-file experiment adapter.",
            "success_criteria": ["Publish a reviewable evidence package"],
            "input_artifact_ids": [],
            "input_path_roots": ["shared/raw"],
            "expected_output_types": ["json", "markdown"],
            "run_parameters": {},
        }
        payload.update(overrides)
        started = self.run_command("run_experiment", payload)
        self.assertTrue(started["ok"], started)
        activation_id = started["result"]["launch_request"]["activation_id"]
        self.assertTrue(self.run_activation("mark-running", project_root, activation_id)["ok"])
        completed = self.run_activation("complete", project_root, activation_id, {"output_artifact_ids": []})
        self.assertTrue(completed["ok"], completed)
        return started["result"]

    def test_run_complete_and_accept_experiment_publishes_reviewable_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(project_root)
            (project_root / "shared" / "raw" / "dataset.md").write_text("# Dataset\n\nSeed evidence.\n", encoding="utf-8")

            started = self.run_command(
                "run_experiment",
                {
                    "root_path": str(project_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "title": "Evaluate baseline",
                    "objective": "Measure the baseline behavior.",
                    "hypothesis": "The baseline is reproducible.",
                    "method": "Run the deterministic local-file adapter.",
                    "success_criteria": ["Publish request, result, summary, and manifest artifacts"],
                    "input_artifact_ids": [],
                    "input_path_roots": ["shared/raw"],
                    "expected_output_types": ["json", "markdown"],
                    "run_parameters": {"trials": 2},
                },
            )

            self.assertTrue(started["ok"], started)
            result = started["result"]
            self.assertTrue(result["admitted"], result)
            launch = result["launch_request"]
            run_id = result["experiment_run"]["experiment_run_id"]
            request_id = result["experiment_request_id"]
            task_id = launch["task_id"]
            activation_id = launch["activation_id"]

            self.assertTrue((project_root / "state" / "experiments" / "requests" / f"{request_id}.json").exists())
            self.assertTrue((project_root / "state" / "experiments" / "runs" / f"{run_id}.json").exists())
            self.assertTrue((project_root / "experiments" / "queue" / request_id / "request.json").exists())
            bundle = json.loads((project_root / launch["bundle_path"]).read_text(encoding="utf-8"))
            self.assertEqual(bundle["experiment_request_id"], request_id)
            self.assertEqual(bundle["experiment_run_id"], run_id)
            self.assertEqual(bundle["run_parameters"], {"trials": 2})
            self.assertIn("experiment_review_required", bundle["review_gates"])

            running = self.run_activation("mark-running", project_root, activation_id)
            self.assertTrue(running["ok"], running)
            run_state = json.loads((project_root / "state" / "experiments" / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(run_state["status"], "running")
            self.assertEqual(run_state["activation_id"], activation_id)

            completed = self.run_activation("complete", project_root, activation_id, {"output_artifact_ids": []})
            self.assertTrue(completed["ok"], completed)
            self.assertGreaterEqual(len(completed["result"]["published_artifact_ids"]), 5)
            task = json.loads((project_root / "state" / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
            run_state = json.loads((project_root / "state" / "experiments" / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(task["status"], "awaiting_review")
            self.assertEqual(run_state["status"], "awaiting_review")
            self.assertTrue((project_root / "experiments" / "runs" / run_id / "outputs" / "result.json").exists())
            self.assertTrue((project_root / "experiments" / "runs" / run_id / "outputs" / "summary.md").exists())

            indexed = {
                artifact["artifact_id"]: artifact
                for artifact in self.artifact_index(project_root)
                if artifact["artifact_id"] in run_state["published_artifact_ids"]
            }
            self.assertEqual(set(indexed), set(run_state["published_artifact_ids"]))
            for artifact in indexed.values():
                self.assertEqual(artifact["visibility"], "project_shared")
                self.assertEqual(artifact["task_id"], task_id)
                self.assertEqual(artifact["producing_activation_id"], activation_id)
                self.assertTrue(artifact["path"].startswith(f"experiments/runs/{run_id}/"))

            reviewed = self.run_command(
                "review_experiment",
                {
                    "root_path": str(project_root),
                    "experiment_run_id": run_id,
                    "reviewer_slot_id": "senior-01",
                    "outcome": "accepted",
                    "decision_summary": "Evidence is sufficient for M7.",
                },
            )

            self.assertTrue(reviewed["ok"], reviewed)
            review = reviewed["result"]["review"]
            self.assertEqual(review["outcome"], "accepted")
            self.assertTrue((project_root / "state" / "experiments" / "reviews" / f"{review['review_id']}.json").exists())
            task = json.loads((project_root / "state" / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
            run_state = json.loads((project_root / "state" / "experiments" / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(task["status"], "completed")
            self.assertEqual(run_state["status"], "reviewed")
            review_artifacts = [
                artifact
                for artifact in self.artifact_index(project_root)
                if artifact["artifact_id"] == review["review_artifact_id"]
            ]
            self.assertEqual(len(review_artifacts), 1)
            self.assertEqual(review_artifacts[0]["type"], "experiment_review")
            self.assertEqual(review_artifacts[0]["visibility"], "project_shared")

            summary = self.run_command(
                "generate_report",
                {"root_path": str(project_root), "report_type": "experiment_summary", "scope_type": "project"},
            )
            self.assertTrue(summary["ok"], summary)
            summary_text = (project_root / "shared" / "reports" / "experiment-summary-latest.md").read_text(encoding="utf-8")
            self.assertIn(run_id, summary_text)
            self.assertIn("accepted", summary_text)
            self.assertNotIn("later roadmap stage", summary_text)

    def test_disabled_experiment_adapter_fails_without_creating_canonical_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(project_root)
            config_path = project_root / "state" / "adapters" / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["experiments"]["enabled"] = False
            config_path.write_text(json.dumps(config), encoding="utf-8")

            started = self.run_command(
                "run_experiment",
                {
                    "root_path": str(project_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "title": "Disabled adapter run",
                    "objective": "This should not create work.",
                    "hypothesis": "Disabled adapters are safe.",
                    "method": "Attempt to run with experiments disabled.",
                    "success_criteria": ["No task state is created"],
                },
                check=False,
            )

            self.assertFalse(started["ok"], started)
            self.assertEqual(started["error"]["code"], "experiment_adapter_unavailable")
            self.assertEqual(list((project_root / "state" / "tasks").glob("*.json")), [])
            self.assertEqual(list((project_root / "state" / "experiments" / "requests").glob("*.json")), [])
            self.assertEqual(list((project_root / "state" / "experiments" / "runs").glob("*.json")), [])
            health = json.loads((project_root / "state" / "adapters" / "health.json").read_text(encoding="utf-8"))
            self.assertEqual(health["experiments"]["status"], "degraded")

    def test_budget_override_approval_replay_updates_experiment_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            approved_root = Path(tmpdir) / "approved-project"
            self.create_project_with_junior(approved_root)
            pending = self.run_command(
                "run_experiment",
                {
                    "root_path": str(approved_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "title": "Approval gated experiment",
                    "objective": "Require approval before admission.",
                    "hypothesis": "Approval replay will admit the task.",
                    "method": "Use a budget override.",
                    "success_criteria": ["Create and approve a checkpoint"],
                    "budget_override": {"token_budget": 1000},
                },
            )
            self.assertTrue(pending["ok"], pending)
            approval_id = pending["result"]["pending_approval"]["approval_id"]
            request_id = pending["result"]["experiment_request_id"]
            run_id = pending["result"]["experiment_run"]["experiment_run_id"]
            task_id = pending["result"]["task"]["task_id"]
            self.assertFalse(pending["result"]["admitted"], pending)
            request = json.loads((approved_root / "state" / "experiments" / "requests" / f"{request_id}.json").read_text())
            self.assertEqual(request["status"], "awaiting_approval")

            approved = self.run_command(
                "approve_checkpoint",
                {
                    "root_path": str(approved_root),
                    "approval_id": approval_id,
                    "decision_summary": "Experiment budget accepted.",
                },
            )

            self.assertTrue(approved["ok"], approved)
            self.assertIsNotNone(approved["result"]["launch_request"])
            task = json.loads((approved_root / "state" / "tasks" / f"{task_id}.json").read_text())
            request = json.loads((approved_root / "state" / "experiments" / "requests" / f"{request_id}.json").read_text())
            run = json.loads((approved_root / "state" / "experiments" / "runs" / f"{run_id}.json").read_text())
            self.assertEqual(task["status"], "admitted")
            self.assertEqual(request["status"], "admitted")
            self.assertEqual(run["activation_id"], approved["result"]["launch_request"]["activation_id"])

            rejected_root = Path(tmpdir) / "rejected-project"
            self.create_project_with_junior(rejected_root)
            rejected_pending = self.run_command(
                "run_experiment",
                {
                    "root_path": str(rejected_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "title": "Rejected experiment",
                    "objective": "Reject approval before admission.",
                    "hypothesis": "Rejected experiment state is cancelled.",
                    "method": "Use a budget override.",
                    "success_criteria": ["Reject the checkpoint"],
                    "budget_override": {"token_budget": 1000},
                },
            )
            rejected_approval_id = rejected_pending["result"]["pending_approval"]["approval_id"]
            rejected_request_id = rejected_pending["result"]["experiment_request_id"]
            rejected_run_id = rejected_pending["result"]["experiment_run"]["experiment_run_id"]

            rejected = self.run_command(
                "reject_checkpoint",
                {
                    "root_path": str(rejected_root),
                    "approval_id": rejected_approval_id,
                    "decision_summary": "Experiment budget rejected.",
                },
            )

            self.assertTrue(rejected["ok"], rejected)
            request = json.loads((rejected_root / "state" / "experiments" / "requests" / f"{rejected_request_id}.json").read_text())
            run = json.loads((rejected_root / "state" / "experiments" / "runs" / f"{rejected_run_id}.json").read_text())
            self.assertEqual(request["status"], "cancelled")
            self.assertEqual(run["status"], "cancelled")

    def test_comparison_outputs_and_follow_up_review_use_standard_task_admission(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(project_root)
            first = self.run_and_publish_experiment(project_root, title="Baseline run")
            first_run_id = first["experiment_run"]["experiment_run_id"]

            second = self.run_and_publish_experiment(
                project_root,
                title="Comparison run",
                compare_run_ids=[first_run_id],
            )
            second_run_id = second["experiment_run"]["experiment_run_id"]
            run = json.loads((project_root / "state" / "experiments" / "runs" / f"{second_run_id}.json").read_text())
            self.assertEqual(len(run["comparison_ids"]), 1)
            comparison_id = run["comparison_ids"][0]
            comparison = json.loads(
                (project_root / "state" / "experiments" / "comparisons" / f"{comparison_id}.json").read_text()
            )
            self.assertEqual(comparison["status"], "completed")
            self.assertEqual(comparison["compared_run_ids"], [first_run_id])
            self.assertEqual(len(comparison["output_artifact_ids"]), 2)

            reviewed = self.run_command(
                "review_experiment",
                {
                    "root_path": str(project_root),
                    "experiment_run_id": second_run_id,
                    "reviewer_slot_id": "senior-01",
                    "outcome": "needs_follow_up",
                    "decision_summary": "Comparison needs one more validation pass.",
                    "follow_up_owner_slot_id": "junior-01",
                    "follow_up_title": "Validate comparison",
                    "follow_up_description": "Use review and comparison artifacts to validate the result.",
                    "follow_up_success_criteria": ["Publish validation notes"],
                },
            )

            self.assertTrue(reviewed["ok"], reviewed)
            follow_up = reviewed["result"]["follow_up_task"]
            self.assertIsNotNone(follow_up)
            follow_up_task_id = follow_up["task_id"]
            follow_up_task = json.loads((project_root / "state" / "tasks" / f"{follow_up_task_id}.json").read_text())
            follow_up_slot = json.loads((project_root / "state" / "slots" / "junior-01.json").read_text())
            self.assertEqual(follow_up_task["status"], "admitted")
            self.assertEqual(follow_up_slot["current_activation_id"], follow_up_task["current_activation_id"])
            self.assertIn(reviewed["result"]["review"]["review_artifact_id"], follow_up_task["input_artifact_ids"])

    def test_follow_up_review_cannot_assign_outside_reviewer_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(project_root)
            added_senior = self.run_command("add_senior", {"root_path": str(project_root)})
            self.assertTrue(added_senior["ok"], added_senior)
            added_junior = self.run_command("add_junior", {"root_path": str(project_root), "parent_slot_id": "senior-02"})
            self.assertTrue(added_junior["ok"], added_junior)
            self.assertEqual(added_junior["result"]["slot"]["slot_id"], "junior-02")
            published = self.run_and_publish_experiment(project_root, title="Scoped follow-up source")

            denied = self.run_command(
                "review_experiment",
                {
                    "root_path": str(project_root),
                    "experiment_run_id": published["experiment_run"]["experiment_run_id"],
                    "reviewer_slot_id": "senior-01",
                    "outcome": "needs_follow_up",
                    "decision_summary": "Attempt cross-branch assignment.",
                    "follow_up_owner_slot_id": "junior-02",
                    "follow_up_title": "Unauthorized follow-up",
                    "follow_up_description": "This owner is not a descendant of senior-01.",
                    "follow_up_success_criteria": ["Do not create this task"],
                },
                check=False,
            )

            self.assertFalse(denied["ok"], denied)
            self.assertEqual(denied["error"]["code"], "requester_not_authorized")
            self.assertEqual(list((project_root / "state" / "experiments" / "reviews").glob("*.json")), [])
            junior_02 = json.loads((project_root / "state" / "slots" / "junior-02.json").read_text(encoding="utf-8"))
            self.assertEqual(junior_02["queued_task_ids"], [])

    def test_plain_experiment_defaults_to_single_run_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(project_root)

            started = self.run_command(
                "run_experiment",
                {
                    "root_path": str(project_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "title": "Single run default",
                    "objective": "Check default experiment run budget.",
                    "hypothesis": "A plain request budgets one run.",
                    "method": "Create a normal experiment request.",
                    "success_criteria": ["Budget one experiment run"],
                },
            )

            self.assertTrue(started["ok"], started)
            task_id = started["result"]["task"]["task_id"]
            request_id = started["result"]["experiment_request_id"]
            task = json.loads((project_root / "state" / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
            request = json.loads((project_root / "state" / "experiments" / "requests" / f"{request_id}.json").read_text())
            self.assertEqual(task["budget_envelope"]["experiment_runs"], 1.0)
            self.assertEqual(request["budget_envelope"]["experiment_runs"], 1.0)

    def test_experiment_run_snapshots_activation_budget_consumption(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(project_root)
            started = self.run_command(
                "run_experiment",
                {
                    "root_path": str(project_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "title": "Budget snapshot",
                    "objective": "Check consumed budget is copied to run state.",
                    "hypothesis": "Heartbeat deltas are durable on the experiment run.",
                    "method": "Heartbeat before publish.",
                    "success_criteria": ["Persist consumed budget"],
                },
            )
            self.assertTrue(started["ok"], started)
            activation_id = started["result"]["launch_request"]["activation_id"]
            run_id = started["result"]["experiment_run"]["experiment_run_id"]
            self.assertTrue(self.run_activation("mark-running", project_root, activation_id)["ok"])
            heartbeat = self.run_activation("heartbeat", project_root, activation_id, {"token_budget": 50})
            self.assertTrue(heartbeat["ok"], heartbeat)
            completed = self.run_activation("complete", project_root, activation_id, {"output_artifact_ids": []})
            self.assertTrue(completed["ok"], completed)

            run = json.loads((project_root / "state" / "experiments" / "runs" / f"{run_id}.json").read_text())
            self.assertEqual(run["consumed_budget"]["token_budget"], 50.0)

    def test_paused_projects_queue_experiments_without_admitting_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(project_root)
            paused = self.run_command("pause_project", {"root_path": str(project_root)})
            self.assertTrue(paused["ok"], paused)

            started = self.run_command(
                "run_experiment",
                {
                    "root_path": str(project_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "title": "Queued while paused",
                    "objective": "Stage experiment work while paused.",
                    "hypothesis": "Paused projects queue but do not admit.",
                    "method": "Create an experiment request while paused.",
                    "success_criteria": ["Preserve queued experiment work"],
                },
            )

            self.assertTrue(started["ok"], started)
            self.assertFalse(started["result"]["admitted"], started)
            self.assertIsNone(started["result"]["launch_request"])
            task_id = started["result"]["task"]["task_id"]
            request_id = started["result"]["experiment_request_id"]
            task = json.loads((project_root / "state" / "tasks" / f"{task_id}.json").read_text())
            request = json.loads((project_root / "state" / "experiments" / "requests" / f"{request_id}.json").read_text())
            slot = json.loads((project_root / "state" / "slots" / "junior-01.json").read_text())
            self.assertEqual(task["status"], "queued")
            self.assertEqual(request["status"], "queued")
            self.assertEqual(slot["queued_task_ids"], [task_id])
            self.assertIsNone(slot["current_activation_id"])

    def test_experiment_run_state_tracks_failure_interruption_and_stale_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            failed_root = Path(tmpdir) / "failed-project"
            self.create_project_with_junior(failed_root)
            failed = self.run_command(
                "run_experiment",
                {
                    "root_path": str(failed_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "title": "Failing experiment",
                    "objective": "Capture failure state.",
                    "hypothesis": "Failure writes a durable summary.",
                    "method": "Fail the activation.",
                    "success_criteria": ["Persist failed experiment state"],
                },
            )
            failed_activation_id = failed["result"]["launch_request"]["activation_id"]
            failed_run_id = failed["result"]["experiment_run"]["experiment_run_id"]
            self.assertTrue(self.run_activation("mark-running", failed_root, failed_activation_id)["ok"])
            failed_result = self.run_activation(
                "fail",
                failed_root,
                failed_activation_id,
                {"failure_summary": "Adapter evidence could not be produced."},
            )
            self.assertTrue(failed_result["ok"], failed_result)
            failed_run = json.loads((failed_root / "state" / "experiments" / "runs" / f"{failed_run_id}.json").read_text())
            self.assertEqual(failed_run["status"], "failed")
            self.assertIsNotNone(failed_run["failure_artifact_id"])

            interrupted_root = Path(tmpdir) / "interrupted-project"
            self.create_project_with_junior(interrupted_root)
            interrupted = self.run_command(
                "run_experiment",
                {
                    "root_path": str(interrupted_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "title": "Interrupted experiment",
                    "objective": "Capture interruption state.",
                    "hypothesis": "Interruption updates experiment state.",
                    "method": "Interrupt the activation.",
                    "success_criteria": ["Persist interrupted experiment state"],
                },
            )
            interrupted_activation_id = interrupted["result"]["launch_request"]["activation_id"]
            interrupted_run_id = interrupted["result"]["experiment_run"]["experiment_run_id"]
            self.assertTrue(self.run_activation("mark-running", interrupted_root, interrupted_activation_id)["ok"])
            interrupted_result = self.run_activation("interrupt", interrupted_root, interrupted_activation_id, {"reason": "user_pause"})
            self.assertTrue(interrupted_result["ok"], interrupted_result)
            interrupted_run = json.loads(
                (interrupted_root / "state" / "experiments" / "runs" / f"{interrupted_run_id}.json").read_text()
            )
            self.assertEqual(interrupted_run["status"], "interrupted")

            stale_root = Path(tmpdir) / "stale-project"
            self.create_project_with_junior(stale_root)
            stale = self.run_command(
                "run_experiment",
                {
                    "root_path": str(stale_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "title": "Stale experiment",
                    "objective": "Recover stale experiment state.",
                    "hypothesis": "Recovery interrupts the run.",
                    "method": "Age the heartbeat and reopen.",
                    "success_criteria": ["Persist interrupted run on recovery"],
                },
            )
            stale_activation_id = stale["result"]["launch_request"]["activation_id"]
            stale_run_id = stale["result"]["experiment_run"]["experiment_run_id"]
            self.assertTrue(self.run_activation("mark-running", stale_root, stale_activation_id)["ok"])
            activation_path = stale_root / "state" / "activations" / f"{stale_activation_id}.json"
            activation = json.loads(activation_path.read_text(encoding="utf-8"))
            activation["lease_heartbeat_at"] = "2000-01-01T00:00:00Z"
            activation["lease_timeout_seconds"] = 1
            activation_path.write_text(json.dumps(activation), encoding="utf-8")

            opened = self.run_command("open_project", {"root_path": str(stale_root)})
            self.assertTrue(opened["ok"], opened)
            stale_run = json.loads((stale_root / "state" / "experiments" / "runs" / f"{stale_run_id}.json").read_text())
            self.assertEqual(stale_run["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
