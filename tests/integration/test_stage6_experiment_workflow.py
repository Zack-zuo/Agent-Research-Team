import json
import os
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

    def publish_started_experiment(self, project_root: Path, started: dict) -> dict:
        activation_id = started["result"]["launch_request"]["activation_id"]
        self.assertTrue(self.run_activation("mark-running", project_root, activation_id)["ok"])
        completed = self.run_activation("complete", project_root, activation_id, {"output_artifact_ids": []})
        self.assertTrue(completed["ok"], completed)
        return completed

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

            scoped_summary = self.run_command(
                "generate_report",
                {
                    "root_path": str(project_root),
                    "report_type": "experiment_summary",
                    "scope_type": "experiment_run",
                    "scope_id": run_id,
                },
            )
            self.assertTrue(scoped_summary["ok"], scoped_summary)
            scoped_text = (project_root / "shared" / "reports" / "experiment-summary-latest.md").read_text(encoding="utf-8")
            self.assertIn(run_id, scoped_text)
            self.assertIn("Experiment Run Count: 1", scoped_text)

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

    def test_local_command_experiment_captures_logs_metrics_outputs_and_git_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(project_root)
            tracked = project_root / "shared" / "raw" / "tracked.txt"
            tracked.write_text("before\n", encoding="utf-8")
            self._git(project_root, "init")
            self._git(project_root, "config", "user.email", "rat@example.test")
            self._git(project_root, "config", "user.name", "RAT Test")
            self._git(project_root, "add", ".")
            self._git(project_root, "commit", "-m", "baseline")
            command_code = "\n".join(
                [
                    "import json, os, pathlib, sys",
                    "out = pathlib.Path(os.environ['RAT_EXPERIMENT_OUTPUT_DIR'])",
                    "out.mkdir(parents=True, exist_ok=True)",
                    "(out / 'generated.txt').write_text('generated evidence\\n', encoding='utf-8')",
                    "pathlib.Path('metrics.json').write_text(json.dumps({'accuracy': 0.875, 'trials': 3}), encoding='utf-8')",
                    "pathlib.Path('tracked.txt').write_text('after\\n', encoding='utf-8')",
                    "print('command stdout line')",
                    "print('command stderr line', file=sys.stderr)",
                ]
            )

            started = self.run_command(
                "run_experiment",
                {
                    "root_path": str(project_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "adapter_type": "local_command",
                    "title": "Local command success",
                    "objective": "Run a real local command and collect evidence.",
                    "hypothesis": "The command adapter records command evidence.",
                    "method": "Execute an argv command in a constrained project working directory.",
                    "success_criteria": ["Publish command logs, metrics, outputs, and git diff"],
                    "input_path_roots": ["shared/raw"],
                    "expected_output_types": ["json", "markdown", "logs"],
                    "run_parameters": {
                        "allow_command_execution": True,
                        "working_directory": "shared/raw",
                        "timeout_seconds": 10,
                        "command": [sys.executable, "-c", command_code],
                        "metrics_files": ["metrics.json"],
                    },
                },
            )
            self.assertTrue(started["ok"], started)
            run_id = started["result"]["experiment_run"]["experiment_run_id"]
            self.publish_started_experiment(project_root, started)

            run_state = json.loads((project_root / "state" / "experiments" / "runs" / f"{run_id}.json").read_text())
            result = json.loads((project_root / "experiments" / "runs" / run_id / "outputs" / "result.json").read_text())
            self.assertEqual(result["adapter_type"], "local_command")
            self.assertEqual(result["command"]["status"], "succeeded")
            self.assertEqual(result["command"]["exit_code"], 0)
            self.assertGreaterEqual(result["command"]["duration_seconds"], 0)
            self.assertEqual(result["metrics"], {"accuracy": 0.875, "trials": 3})
            self.assertEqual(result["git"]["before"]["is_git_repository"], True)
            self.assertEqual(result["git"]["after"]["is_dirty"], True)
            self.assertIn("tracked.txt", result["git"]["diff_summary"])
            self.assertEqual(result["diagnostics"], [])

            run_root = project_root / "experiments" / "runs" / run_id
            self.assertIn("command stdout line", (run_root / "logs" / "stdout.log").read_text(encoding="utf-8"))
            self.assertIn("command stderr line", (run_root / "logs" / "stderr.log").read_text(encoding="utf-8"))
            self.assertEqual((run_root / "outputs" / "generated" / "generated.txt").read_text(encoding="utf-8"), "generated evidence\n")
            self.assertIn("after", (run_root / "git" / "diff.patch").read_text(encoding="utf-8"))

            indexed = [artifact for artifact in self.artifact_index(project_root) if artifact["artifact_id"] in run_state["published_artifact_ids"]]
            indexed_types = {artifact["type"] for artifact in indexed}
            self.assertIn("experiment_stdout_log", indexed_types)
            self.assertIn("experiment_stderr_log", indexed_types)
            self.assertIn("experiment_metrics", indexed_types)
            self.assertIn("experiment_generated_output", indexed_types)
            self.assertIn("experiment_git_diff", indexed_types)

    def test_local_command_experiment_records_failed_exit_without_activation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(project_root)
            command_code = "import sys; print('before failure'); print('bad path', file=sys.stderr); raise SystemExit(3)"

            started = self.run_command(
                "run_experiment",
                {
                    "root_path": str(project_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "adapter_type": "local_command",
                    "title": "Local command failure",
                    "objective": "Record a non-zero command exit.",
                    "hypothesis": "Failed commands produce reviewable evidence.",
                    "method": "Run a command that exits non-zero.",
                    "success_criteria": ["Publish failed command evidence"],
                    "run_parameters": {
                        "allow_command_execution": True,
                        "command": [sys.executable, "-c", command_code],
                        "timeout_seconds": 10,
                    },
                },
            )
            self.assertTrue(started["ok"], started)
            run_id = started["result"]["experiment_run"]["experiment_run_id"]
            self.publish_started_experiment(project_root, started)

            run_state = json.loads((project_root / "state" / "experiments" / "runs" / f"{run_id}.json").read_text())
            result = json.loads((project_root / "experiments" / "runs" / run_id / "outputs" / "result.json").read_text())
            self.assertEqual(run_state["status"], "awaiting_review")
            self.assertEqual(result["command"]["status"], "failed")
            self.assertEqual(result["command"]["exit_code"], 3)
            self.assertIn("bad path", (project_root / "experiments" / "runs" / run_id / "logs" / "stderr.log").read_text(encoding="utf-8"))

    def test_local_command_experiment_records_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(project_root)
            command_code = "import time; print('starting timeout', flush=True); time.sleep(3)"

            started = self.run_command(
                "run_experiment",
                {
                    "root_path": str(project_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "adapter_type": "local_command",
                    "title": "Local command timeout",
                    "objective": "Record timeout behavior.",
                    "hypothesis": "Timed out commands produce diagnostics.",
                    "method": "Run a command past its timeout.",
                    "success_criteria": ["Publish timeout evidence"],
                    "run_parameters": {
                        "allow_command_execution": True,
                        "command": [sys.executable, "-c", command_code],
                        "timeout_seconds": 1,
                    },
                },
            )
            self.assertTrue(started["ok"], started)
            run_id = started["result"]["experiment_run"]["experiment_run_id"]
            self.publish_started_experiment(project_root, started)

            result = json.loads((project_root / "experiments" / "runs" / run_id / "outputs" / "result.json").read_text())
            self.assertEqual(result["command"]["status"], "timed_out")
            self.assertIsNone(result["command"]["exit_code"])
            self.assertTrue(any(diagnostic["code"] == "command_timeout" for diagnostic in result["diagnostics"]))

    def test_local_command_experiment_keeps_metrics_parse_failures_non_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(project_root)
            command_code = "from pathlib import Path; Path('metrics.txt').write_text('not a metric line\\n', encoding='utf-8')"

            started = self.run_command(
                "run_experiment",
                {
                    "root_path": str(project_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "adapter_type": "local_command",
                    "title": "Bad metrics",
                    "objective": "Keep metrics parse failures visible but non-fatal.",
                    "hypothesis": "The run can still publish evidence.",
                    "method": "Write an invalid key-value metrics file.",
                    "success_criteria": ["Publish diagnostics"],
                    "input_path_roots": ["shared/raw"],
                    "run_parameters": {
                        "allow_command_execution": True,
                        "working_directory": "shared/raw",
                        "command": [sys.executable, "-c", command_code],
                        "metrics_files": ["metrics.txt"],
                        "timeout_seconds": 10,
                    },
                },
            )
            self.assertTrue(started["ok"], started)
            run_id = started["result"]["experiment_run"]["experiment_run_id"]
            self.publish_started_experiment(project_root, started)

            result = json.loads((project_root / "experiments" / "runs" / run_id / "outputs" / "result.json").read_text())
            self.assertEqual(result["command"]["status"], "succeeded")
            self.assertEqual(result["metrics"], {})
            self.assertTrue(any(diagnostic["code"] == "metrics_parse_failed" for diagnostic in result["diagnostics"]))

    def test_local_command_experiment_requires_explicit_execution_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(project_root)

            started = self.run_command(
                "run_experiment",
                {
                    "root_path": str(project_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "adapter_type": "local_command",
                    "title": "Unsafe command",
                    "objective": "Reject command execution without explicit opt in.",
                    "method": "Attempt to run a command without allow_command_execution.",
                    "success_criteria": ["Do not create canonical experiment work"],
                    "run_parameters": {"command": [sys.executable, "-c", "print('nope')"]},
                },
                check=False,
            )

            self.assertFalse(started["ok"], started)
            self.assertEqual(started["error"]["code"], "experiment_command_not_allowed")
            self.assertEqual(list((project_root / "state" / "tasks").glob("*.json")), [])

    def test_local_command_experiment_records_missing_executable_as_reviewable_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(project_root)

            started = self.run_command(
                "run_experiment",
                {
                    "root_path": str(project_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "adapter_type": "local_command",
                    "title": "Missing executable",
                    "objective": "Record command launch failures as experiment evidence.",
                    "method": "Run a command whose executable is missing.",
                    "success_criteria": ["Publish launch failure diagnostics"],
                    "run_parameters": {
                        "allow_command_execution": True,
                        "command": ["rat-missing-executable-for-test"],
                        "timeout_seconds": 10,
                    },
                },
            )
            self.assertTrue(started["ok"], started)
            run_id = started["result"]["experiment_run"]["experiment_run_id"]
            self.publish_started_experiment(project_root, started)

            run_state = json.loads((project_root / "state" / "experiments" / "runs" / f"{run_id}.json").read_text())
            result = json.loads((project_root / "experiments" / "runs" / run_id / "outputs" / "result.json").read_text())
            self.assertEqual(run_state["status"], "awaiting_review")
            self.assertEqual(result["command"]["status"], "failed")
            self.assertIsNone(result["command"]["exit_code"])
            self.assertTrue(any(diagnostic["code"] == "command_launch_failed" for diagnostic in result["diagnostics"]))

    def test_local_command_experiment_captures_staged_git_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(project_root)
            tracked = project_root / "shared" / "raw" / "tracked.txt"
            tracked.write_text("before\n", encoding="utf-8")
            self._git(project_root, "init")
            self._git(project_root, "config", "user.email", "rat@example.test")
            self._git(project_root, "config", "user.name", "RAT Test")
            self._git(project_root, "add", ".")
            self._git(project_root, "commit", "-m", "baseline")
            command_code = "\n".join(
                [
                    "import pathlib, subprocess",
                    "pathlib.Path('tracked.txt').write_text('after staged\\n', encoding='utf-8')",
                    "subprocess.run(['git', 'add', 'tracked.txt'], check=True)",
                ]
            )

            started = self.run_command(
                "run_experiment",
                {
                    "root_path": str(project_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "adapter_type": "local_command",
                    "title": "Staged git diff",
                    "objective": "Capture staged changes in git diff evidence.",
                    "method": "Modify and stage a tracked file.",
                    "success_criteria": ["Publish staged git diff"],
                    "input_path_roots": ["shared/raw"],
                    "run_parameters": {
                        "allow_command_execution": True,
                        "working_directory": "shared/raw",
                        "command": [sys.executable, "-c", command_code],
                        "timeout_seconds": 10,
                    },
                },
            )
            self.assertTrue(started["ok"], started)
            run_id = started["result"]["experiment_run"]["experiment_run_id"]
            self.publish_started_experiment(project_root, started)

            result = json.loads((project_root / "experiments" / "runs" / run_id / "outputs" / "result.json").read_text())
            self.assertIn("tracked.txt", result["git"]["diff_summary"])
            self.assertIn("after staged", (project_root / "experiments" / "runs" / run_id / "git" / "diff.patch").read_text(encoding="utf-8"))

    def test_local_command_experiment_keeps_binary_metrics_decode_failures_non_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project_with_junior(project_root)
            command_code = "from pathlib import Path; Path('metrics.json').write_bytes(b'\\xff\\xfe')"

            started = self.run_command(
                "run_experiment",
                {
                    "root_path": str(project_root),
                    "requester_slot_id": "senior-01",
                    "executor_slot_id": "junior-01",
                    "adapter_type": "local_command",
                    "title": "Binary metrics",
                    "objective": "Keep binary metrics files non-fatal.",
                    "method": "Write an invalid UTF-8 metrics file.",
                    "success_criteria": ["Publish metrics decode diagnostic"],
                    "input_path_roots": ["shared/raw"],
                    "run_parameters": {
                        "allow_command_execution": True,
                        "working_directory": "shared/raw",
                        "command": [sys.executable, "-c", command_code],
                        "metrics_files": ["metrics.json"],
                        "timeout_seconds": 10,
                    },
                },
            )
            self.assertTrue(started["ok"], started)
            run_id = started["result"]["experiment_run"]["experiment_run_id"]
            self.publish_started_experiment(project_root, started)

            run_state = json.loads((project_root / "state" / "experiments" / "runs" / f"{run_id}.json").read_text())
            result = json.loads((project_root / "experiments" / "runs" / run_id / "outputs" / "result.json").read_text())
            self.assertEqual(run_state["status"], "awaiting_review")
            self.assertEqual(result["metrics"], {})
            self.assertTrue(any(diagnostic["code"] == "metrics_parse_failed" for diagnostic in result["diagnostics"]))

    def _git(self, project_root: Path, *args: str) -> None:
        env = os.environ.copy()
        env.setdefault("GIT_AUTHOR_NAME", "RAT Test")
        env.setdefault("GIT_AUTHOR_EMAIL", "rat@example.test")
        env.setdefault("GIT_COMMITTER_NAME", "RAT Test")
        env.setdefault("GIT_COMMITTER_EMAIL", "rat@example.test")
        result = subprocess.run(
            ["git", "-C", str(project_root), *args],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            self.fail(f"git {' '.join(args)} failed\nstdout={result.stdout}\nstderr={result.stderr}")


if __name__ == "__main__":
    unittest.main()
