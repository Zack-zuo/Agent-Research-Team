import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "research-agent-team"
SCRIPT = PLUGIN_ROOT / "scripts" / "rat_plugin_cli.py"


class Stage5KnowledgeGraphTests(unittest.TestCase):
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
                "name": "Stage 5 Demo",
                "root_path": str(project_root),
                "initial_charter_text": "Knowledge and graph project.",
            },
        )
        self.assertTrue(payload["ok"], payload)

    def artifacts_for_path(self, project_root: Path, relative_path: str) -> list[dict]:
        index_path = project_root / "state" / "artifacts" / "index.jsonl"
        artifacts = [
            json.loads(line)
            for line in index_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return [artifact for artifact in artifacts if artifact["path"] == relative_path]

    def test_slot_and_project_sync_promote_traceable_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            raw_source = project_root / "shared" / "raw" / "paper-a.md"
            workspace_note = project_root / "agents" / "senior-01" / "workspace" / "lit-note.md"
            raw_source.write_text("# Paper A\n\nUses [[method alpha]].\n", encoding="utf-8")
            workspace_note.write_text(
                "# Literature Note\n\nSee [Paper A](../../../shared/raw/paper-a.md).\n",
                encoding="utf-8",
            )

            slot_sync = self.run_command(
                "sync_knowledge_base",
                {
                    "root_path": str(project_root),
                    "scope_type": "slot",
                    "scope_id": "senior-01",
                    "mode": "full",
                },
            )
            self.assertTrue(slot_sync["ok"], slot_sync)
            self.assertGreaterEqual(slot_sync["result"]["source_count"], 2)
            self.assertGreaterEqual(slot_sync["result"]["compiled_count"], 2)
            slot_index = project_root / "agents" / "senior-01" / "kb" / "wiki" / "index.md"
            self.assertTrue(slot_index.exists())
            self.assertIn("agents/senior-01/workspace/lit-note.md", slot_index.read_text(encoding="utf-8"))

            slot_state = json.loads(
                (project_root / "state" / "knowledge" / "slots" / "senior-01.json").read_text(encoding="utf-8")
            )
            self.assertTrue(slot_state["graph_dirty"])
            self.assertIn("shared/raw/paper-a.md", slot_state["source_hashes"])
            self.assertTrue(slot_state["compiled_artifact_ids_by_output_path"])

            project_sync = self.run_command(
                "sync_knowledge_base",
                {
                    "root_path": str(project_root),
                    "scope_type": "project",
                    "mode": "incremental",
                },
            )
            self.assertTrue(project_sync["ok"], project_sync)
            self.assertGreaterEqual(project_sync["result"]["compiled_count"], 1)
            shared_index = project_root / "shared" / "wiki" / "index.md"
            self.assertTrue(shared_index.exists())
            self.assertIn("senior-01", shared_index.read_text(encoding="utf-8"))

            project_state = json.loads((project_root / "state" / "knowledge" / "project.json").read_text(encoding="utf-8"))
            self.assertTrue(project_state["graph_dirty"])
            self.assertTrue(project_state["compiled_artifact_ids_by_output_path"])
            shared_artifacts = self.artifacts_for_path(project_root, "shared/wiki/index.md")
            self.assertTrue(shared_artifacts)
            self.assertEqual(shared_artifacts[-1]["visibility"], "project_shared")

    def test_rebuild_graph_writes_exports_and_clears_dirty_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            (project_root / "shared" / "raw" / "paper-a.md").write_text(
                "# Paper A\n\nLinks to [Paper B](paper-b.md).\n",
                encoding="utf-8",
            )
            (project_root / "shared" / "raw" / "paper-b.md").write_text("# Paper B\n\nEvidence.\n", encoding="utf-8")
            self.run_command("sync_knowledge_base", {"root_path": str(project_root), "scope_type": "project", "mode": "full"})

            rebuilt = self.run_command("rebuild_graph", {"root_path": str(project_root), "mode": "full"})

            self.assertTrue(rebuilt["ok"], rebuilt)
            result = rebuilt["result"]
            self.assertFalse(result["degraded"], result)
            self.assertEqual(result["export_path"], "shared/graph/graph-export-latest.json")
            self.assertEqual(result["report_path"], "shared/graph/graph-report-latest.md")
            export = json.loads((project_root / result["export_path"]).read_text(encoding="utf-8"))
            self.assertGreaterEqual(export["node_count"], 1)
            self.assertEqual(export["adapter_id"], "local_file")
            self.assertTrue(self.artifacts_for_path(project_root, "shared/graph/graph-export-latest.json"))
            self.assertTrue(self.artifacts_for_path(project_root, "shared/graph/graph-report-latest.md"))
            project_state = json.loads((project_root / "state" / "knowledge" / "project.json").read_text(encoding="utf-8"))
            self.assertFalse(project_state["graph_dirty"])
            health = json.loads((project_root / "state" / "adapters" / "health.json").read_text(encoding="utf-8"))
            self.assertEqual(health["graph"]["status"], "healthy")

    def test_disabled_graph_adapter_degrades_without_corrupting_canonical_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            config_path = project_root / "state" / "adapters" / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["graph"]["enabled"] = False
            config_path.write_text(json.dumps(config), encoding="utf-8")
            before_tasks = sorted((project_root / "state" / "tasks").glob("*.json"))
            before_approvals = sorted((project_root / "state" / "approvals").glob("*.json"))
            before_activations = sorted((project_root / "state" / "activations").glob("*.json"))

            rebuilt = self.run_command("rebuild_graph", {"root_path": str(project_root), "mode": "incremental"})

            self.assertTrue(rebuilt["ok"], rebuilt)
            self.assertTrue(rebuilt["result"]["degraded"], rebuilt)
            self.assertIn("disabled", " ".join(rebuilt["result"]["warnings"]))
            self.assertFalse((project_root / "shared" / "graph" / "graph-export-latest.json").exists())
            self.assertEqual(before_tasks, sorted((project_root / "state" / "tasks").glob("*.json")))
            self.assertEqual(before_approvals, sorted((project_root / "state" / "approvals").glob("*.json")))
            self.assertEqual(before_activations, sorted((project_root / "state" / "activations").glob("*.json")))
            health = json.loads((project_root / "state" / "adapters" / "health.json").read_text(encoding="utf-8"))
            self.assertEqual(health["graph"]["status"], "degraded")

    def test_literature_report_and_final_package_include_stage5_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            (project_root / "shared" / "raw" / "paper-a.md").write_text("# Paper A\n\nImportant result.\n", encoding="utf-8")
            self.run_command("sync_knowledge_base", {"root_path": str(project_root), "scope_type": "project", "mode": "full"})
            self.run_command("rebuild_graph", {"root_path": str(project_root), "mode": "full"})

            literature = self.run_command(
                "generate_report",
                {"root_path": str(project_root), "report_type": "literature_review", "scope_type": "project"},
            )
            self.assertTrue(literature["ok"], literature)
            lit_text = (project_root / "shared" / "reports" / "literature-review-latest.md").read_text(encoding="utf-8")
            self.assertIn("shared/wiki/index.md", lit_text)
            self.assertNotIn("not available until the later roadmap stage", lit_text)

            approvals_path = project_root / "state" / "policies" / "approvals.json"
            approvals = json.loads(approvals_path.read_text(encoding="utf-8"))
            approvals["final_package"] = "immediate"
            approvals_path.write_text(json.dumps(approvals), encoding="utf-8")
            final_package = self.run_command(
                "generate_report",
                {"root_path": str(project_root), "report_type": "final_package", "scope_type": "project"},
            )
            self.assertTrue(final_package["ok"], final_package)
            final_text = (project_root / "shared" / "reports" / "final-package-latest.md").read_text(encoding="utf-8")
            self.assertIn("Graph Report: shared/graph/graph-report-latest.md", final_text)

    def test_full_project_sync_removes_deleted_source_pages_from_wiki_and_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            keep_source = project_root / "shared" / "raw" / "keep.md"
            stale_source = project_root / "shared" / "raw" / "stale.md"
            keep_source.write_text("# Keep\n\nCurrent evidence.\n", encoding="utf-8")
            stale_source.write_text("# Stale\n\nOld evidence.\n", encoding="utf-8")
            self.run_command("sync_knowledge_base", {"root_path": str(project_root), "scope_type": "project", "mode": "full"})
            stale_page = project_root / "shared" / "wiki" / "shared" / "raw" / "stale.md"
            self.assertTrue(stale_page.exists())

            stale_source.unlink()
            self.run_command("sync_knowledge_base", {"root_path": str(project_root), "scope_type": "project", "mode": "full"})
            self.assertFalse(stale_page.exists())
            rebuilt = self.run_command("rebuild_graph", {"root_path": str(project_root), "mode": "full"})
            export = json.loads((project_root / rebuilt["result"]["export_path"]).read_text(encoding="utf-8"))
            node_paths = {node["path"] for node in export["nodes"]}
            self.assertIn("shared/wiki/shared/raw/keep.md", node_paths)
            self.assertNotIn("shared/wiki/shared/raw/stale.md", node_paths)

    def test_literature_report_uses_current_project_knowledge_state_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            keep_source = project_root / "shared" / "raw" / "keep.md"
            stale_source = project_root / "shared" / "raw" / "stale.md"
            keep_source.write_text("# Keep\n\nCurrent evidence.\n", encoding="utf-8")
            stale_source.write_text("# Stale\n\nOld evidence.\n", encoding="utf-8")
            self.run_command("sync_knowledge_base", {"root_path": str(project_root), "scope_type": "project", "mode": "full"})
            stale_source.unlink()
            self.run_command("sync_knowledge_base", {"root_path": str(project_root), "scope_type": "project", "mode": "full"})

            literature = self.run_command(
                "generate_report",
                {"root_path": str(project_root), "report_type": "literature_review", "scope_type": "project"},
            )
            self.assertTrue(literature["ok"], literature)
            lit_text = (project_root / "shared" / "reports" / "literature-review-latest.md").read_text(encoding="utf-8")
            self.assertIn("shared/wiki/shared/raw/keep.md", lit_text)
            self.assertNotIn("shared/wiki/shared/raw/stale.md", lit_text)
            self.assertNotIn("Knowledge artifact missing on disk", lit_text)

    def test_rebuild_graph_includes_all_supported_markdown_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "rat-project"
            self.create_project(project_root)
            (project_root / "shared" / "raw" / "paper.markdown").write_text(
                "# Markdown Source\n\nLinks to [Index](index.md).\n",
                encoding="utf-8",
            )
            self.run_command("sync_knowledge_base", {"root_path": str(project_root), "scope_type": "project", "mode": "full"})

            rebuilt = self.run_command("rebuild_graph", {"root_path": str(project_root), "mode": "full"})

            export = json.loads((project_root / rebuilt["result"]["export_path"]).read_text(encoding="utf-8"))
            node_paths = {node["path"] for node in export["nodes"]}
            self.assertIn("shared/wiki/shared/raw/paper.markdown", node_paths)

    def test_sync_knowledge_schema_matches_runtime_scope_id_rules(self) -> None:
        schema = json.loads((PLUGIN_ROOT / "schemas" / "commands" / "sync-knowledge-base.schema.json").read_text(encoding="utf-8"))

        self.assertEqual(set(schema["properties"]["scope_id"]["type"]), {"string", "null"})
        self.assertNotIn("scope_id", schema["required"])
        slot_rules = [
            rule
            for rule in schema.get("allOf", [])
            if rule.get("if", {}).get("properties", {}).get("scope_type", {}).get("const") == "slot"
        ]
        self.assertEqual(len(slot_rules), 1)
        self.assertIn("scope_id", slot_rules[0]["then"]["required"])


if __name__ == "__main__":
    unittest.main()
