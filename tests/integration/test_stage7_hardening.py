import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "research-agent-team"
SCRIPT = PLUGIN_ROOT / "scripts" / "rat_plugin_cli.py"


class Stage7HardeningTests(unittest.TestCase):
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

    def create_project(self, project_root: Path) -> None:
        payload = self.run_command(
            "create_project",
            {
                "name": "Stage 7 Demo",
                "root_path": str(project_root),
                "initial_charter_text": "Hardening project.",
            },
        )
        self.assertTrue(payload["ok"], payload)

    def assign_task(self, project_root: Path) -> dict:
        payload = self.run_command(
            "assign_task",
            {
                "root_path": str(project_root),
                "requester_slot_id": "supervisor",
                "owner_slot_id": "senior-01",
                "title": "Preflight task",
                "description": "Exercise Stage 7 shared preflight.",
                "success_criteria": ["Return preflight warnings"],
                "input_artifact_ids": [],
                "input_path_roots": ["shared/raw"],
                "expected_output_types": ["markdown"],
            },
        )
        self.assertTrue(payload["ok"], payload)
        return payload["result"]

    def test_open_project_migrates_010_with_backups_records_and_health_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)

            manifest_path = project_root / "project.yaml"
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            manifest["schema_version"] = "0.1.0"
            manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

            project_path = project_root / "state" / "project.json"
            project = json.loads(project_path.read_text(encoding="utf-8"))
            project["schema_version"] = "0.1.0"
            project_path.write_text(json.dumps(project, indent=2), encoding="utf-8")

            health_path = project_root / "state" / "adapters" / "health.json"
            health_path.write_text(
                json.dumps({"graph": {"state": "failed", "last_error": "graph adapter crashed"}, "experiments": "unknown"}),
                encoding="utf-8",
            )
            (project_root / "state" / "hooks" / "config.json").unlink()

            opened = self.run_command("open_project", {"root_path": str(project_root)})

            self.assertTrue(opened["ok"], opened)
            result = opened["result"]
            self.assertTrue(result["migration_performed"])
            self.assertEqual(result["previous_schema_version"], "0.1.0")
            self.assertEqual(result["project"]["schema_version"], "0.2.0")
            self.assertTrue((project_root / "state" / "hooks" / "config.json").exists())
            self.assertTrue(any("migrated schema" in warning.lower() for warning in result["warnings"]))

            records = list((project_root / "state" / "migrations" / "records").glob("*.json"))
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_text(encoding="utf-8"))
            self.assertEqual(record["from_schema_version"], "0.1.0")
            self.assertEqual(record["to_schema_version"], "0.2.0")
            self.assertEqual(record["status"], "completed")
            self.assertTrue((project_root / record["backup_root"] / "project.yaml").exists())
            self.assertTrue((project_root / record["backup_root"] / "state" / "project.json").exists())
            normalized_health = json.loads(health_path.read_text(encoding="utf-8"))
            self.assertEqual(normalized_health["graph"]["status"], "degraded")
            self.assertIn("graph adapter crashed", normalized_health["graph"]["message"])

    def test_unsupported_schema_fails_fast_without_migration_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            manifest_path = project_root / "project.yaml"
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            manifest["schema_version"] = "9.9.9"
            manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

            opened = self.run_command("open_project", {"root_path": str(project_root)}, check=False)

            self.assertFalse(opened["ok"], opened)
            self.assertEqual(opened["error"]["code"], "unsupported_schema_version")
            self.assertFalse(any((project_root / "state" / "migrations" / "records").glob("*.json")))

    def test_preflight_repairs_support_surfaces_and_surfaces_warnings_on_mutating_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            hook_config = project_root / "state" / "hooks" / "config.json"
            inbox = project_root / "agents" / "senior-01" / "inbox"
            hook_config.unlink()
            inbox.rmdir()

            result = self.assign_task(project_root)

            self.assertTrue(hook_config.exists())
            self.assertTrue(inbox.exists())
            self.assertTrue(any("state/hooks/config.json" in warning for warning in result["warnings"]))
            self.assertTrue(any("agents/senior-01/inbox" in warning for warning in result["warnings"]))

    def test_preflight_clamps_adapter_status_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            health_path = project_root / "state" / "adapters" / "health.json"
            health_path.write_text(
                json.dumps(
                    {
                        "graph": {"status": "failed", "message": "graph adapter failed"},
                        "experiments": {"status": "unavailable", "message": "experiment adapter unavailable"},
                    }
                ),
                encoding="utf-8",
            )

            opened = self.run_command("open_project", {"root_path": str(project_root)})

            self.assertTrue(opened["ok"], opened)
            result = opened["result"]
            self.assertEqual(result["adapter_health"]["graph"]["status"], "degraded")
            self.assertEqual(result["adapter_health"]["experiments"]["status"], "degraded")
            normalized_health = json.loads(health_path.read_text(encoding="utf-8"))
            self.assertEqual(normalized_health["graph"]["status"], "degraded")
            self.assertEqual(normalized_health["experiments"]["status"], "degraded")

    def test_preflight_blocks_fatal_integrity_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            task_path = project_root / "state" / "tasks" / "task-broken.json"
            task_path.write_text(
                json.dumps(
                    {
                        "task_id": "task-broken",
                        "project_id": "project-broken",
                        "requester_slot_id": "supervisor",
                        "owner_slot_id": "missing-slot",
                        "status": "queued",
                        "title": "Broken",
                        "description": "References a missing owner.",
                        "success_criteria": [],
                    }
                ),
                encoding="utf-8",
            )

            opened = self.run_command("open_project", {"root_path": str(project_root)}, check=False)

            self.assertFalse(opened["ok"], opened)
            self.assertEqual(opened["error"]["code"], "preflight_integrity_error")
            self.assertIn("missing owner slot", opened["error"]["message"])

    def test_hook_delivery_logs_success_and_failure_without_rolling_back_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            hook_output_path = project_root / "logs" / "hook-payload.json"
            hook_config = {
                "subscribers": [
                    {
                        "subscriber_id": "capture-report-generated",
                        "enabled": True,
                        "event_types": ["report.generated"],
                        "command_argv": [
                            sys.executable,
                            "-c",
                            "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(), encoding='utf-8')",
                            str(hook_output_path),
                        ],
                        "working_directory": str(project_root),
                        "timeout_seconds": 10,
                    },
                    {
                        "subscriber_id": "fail-report-generated",
                        "enabled": True,
                        "event_types": ["report.generated"],
                        "command_argv": [sys.executable, "-c", "import sys; sys.exit(7)"],
                        "working_directory": str(project_root),
                        "timeout_seconds": 10,
                    },
                ]
            }
            (project_root / "state" / "hooks" / "config.json").write_text(json.dumps(hook_config), encoding="utf-8")

            status = self.run_command("request_status", {"root_path": str(project_root)})

            self.assertTrue(status["ok"], status)
            result = status["result"]
            self.assertEqual(result["report_path"], "shared/reports/status-latest.md")
            self.assertTrue((project_root / result["report_path"]).exists())
            self.assertTrue(hook_output_path.exists())
            envelope = json.loads(hook_output_path.read_text(encoding="utf-8"))
            self.assertEqual(envelope["event"]["event_type"], "report.generated")
            self.assertEqual(envelope["root_path"], str(project_root.resolve()))
            self.assertTrue(any("fail-report-generated" in warning for warning in result["warnings"]))
            hook_log_text = "\n".join(path.read_text(encoding="utf-8") for path in (project_root / "logs" / "hooks").glob("*.jsonl"))
            self.assertIn('"subscriber_id": "capture-report-generated"', hook_log_text)
            self.assertIn('"status": "delivered"', hook_log_text)
            self.assertIn('"subscriber_id": "fail-report-generated"', hook_log_text)
            self.assertIn('"status": "failed"', hook_log_text)
            event_text = "\n".join(path.read_text(encoding="utf-8") for path in (project_root / "state" / "events").glob("*.jsonl"))
            self.assertIn("hook.delivered", event_text)
            self.assertIn("hook.failed", event_text)

    def test_assign_task_surfaces_task_created_hook_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            hook_config = {
                "subscribers": [
                    {
                        "subscriber_id": "fail-task-created",
                        "enabled": True,
                        "event_types": ["task.created"],
                        "command_argv": [sys.executable, "-c", "import sys; sys.exit(9)"],
                        "working_directory": str(project_root),
                        "timeout_seconds": 10,
                    }
                ]
            }
            (project_root / "state" / "hooks" / "config.json").write_text(json.dumps(hook_config), encoding="utf-8")

            result = self.assign_task(project_root)

            self.assertTrue(any("fail-task-created" in warning for warning in result["warnings"]))
            hook_log_text = "\n".join(path.read_text(encoding="utf-8") for path in (project_root / "logs" / "hooks").glob("*.jsonl"))
            self.assertIn('"subscriber_id": "fail-task-created"', hook_log_text)
            self.assertIn('"status": "failed"', hook_log_text)

    def test_malformed_hook_subscriber_is_reported_as_hook_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            hook_config = {
                "subscribers": [
                    {
                        "subscriber_id": "bad-report-hook",
                        "enabled": True,
                        "event_types": ["report.generated"],
                        "command_argv": [],
                        "working_directory": str(project_root),
                        "timeout_seconds": 10,
                    }
                ]
            }
            (project_root / "state" / "hooks" / "config.json").write_text(json.dumps(hook_config), encoding="utf-8")

            status = self.run_command("request_status", {"root_path": str(project_root)})

            self.assertTrue(status["ok"], status)
            result = status["result"]
            self.assertEqual(result["report_path"], "shared/reports/status-latest.md")
            self.assertTrue(any("bad-report-hook" in warning for warning in result["warnings"]))
            hook_log_text = "\n".join(path.read_text(encoding="utf-8") for path in (project_root / "logs" / "hooks").glob("*.jsonl"))
            self.assertIn('"subscriber_id": "bad-report-hook"', hook_log_text)
            self.assertIn('"status": "failed"', hook_log_text)
            event_text = "\n".join(path.read_text(encoding="utf-8") for path in (project_root / "state" / "events").glob("*.jsonl"))
            self.assertIn("hook.failed", event_text)


if __name__ == "__main__":
    unittest.main()
