import json
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "research-agent-team"
SCRIPT = PLUGIN_ROOT / "scripts" / "rat_plugin_cli.py"


class Stage0PluginSkeletonTests(unittest.TestCase):
    def run_plugin_script(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=PLUGIN_ROOT,
            text=True,
            capture_output=True,
            check=check,
        )

    def test_stage0_files_are_real_assets_not_placeholders(self) -> None:
        required_assets = [
            ".codex-plugin/plugin.json",
            "README.md",
            "LICENSE",
            "hooks/README.md",
            "hooks/hooks.json",
            "mcp/.mcp.json",
            "mcp/README.md",
            "prompts/supervisor.md",
            "prompts/senior_phd.md",
            "prompts/junior_phd.md",
            "prompts/activation_worker.md",
            "prompts/report_writer.md",
            "prompts/experiment_reviewer.md",
            "scripts/rat_plugin_cli.py",
            "scripts/rat_plugin_mcp.py",
            "scripts/render_launch_prompt.py",
            "scripts/validate_manifest.py",
            "scripts/validate_schemas.py",
            "skills/research-agent-team/SKILL.md",
            "skills/research-agent-team/references/command-quick-reference.md",
            "skills/research-agent-team/references/worker-callback-contract.md",
        ]

        for relative_path in required_assets:
            path = PLUGIN_ROOT / relative_path
            self.assertTrue(path.exists(), relative_path)
            self.assertGreater(path.stat().st_size, 20, relative_path)

    def test_manifest_is_valid_and_paths_stay_inside_plugin_root(self) -> None:
        manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["name"], "research-agent-team")
        self.assertEqual(manifest["version"], "0.2.0")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["mcpServers"], "./mcp/.mcp.json")

        skills_path = (PLUGIN_ROOT / manifest["skills"]).resolve()
        self.assertTrue(skills_path.exists())
        self.assertTrue(skills_path == PLUGIN_ROOT.resolve() or PLUGIN_ROOT.resolve() in skills_path.parents)
        mcp_path = (PLUGIN_ROOT / manifest["mcpServers"]).resolve()
        self.assertTrue(mcp_path.exists())
        self.assertTrue(mcp_path == PLUGIN_ROOT.resolve() or PLUGIN_ROOT.resolve() in mcp_path.parents)

    def test_cli_exposes_command_surface_and_stage6_runtime_handlers(self) -> None:
        top_level_help = self.run_plugin_script("--help")
        self.assertIn("usage:", top_level_help.stdout)

        command_help = self.run_plugin_script("command", "--help")
        self.assertIn("create_project", command_help.stdout)
        self.assertIn("assign_task", command_help.stdout)
        self.assertIn("rebuild_graph", command_help.stdout)
        self.assertIn("run_experiment", command_help.stdout)
        self.assertIn("review_experiment", command_help.stdout)

        known = self.run_plugin_script("command", "run_experiment", "--payload-json", "{}", check=False)
        self.assertEqual(known.returncode, 1)
        payload = json.loads(known.stdout)
        self.assertNotEqual(payload["error"]["code"], "not_implemented")

        unknown = self.run_plugin_script("command", "not_a_command", "--payload-json", "{}", check=False)
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn("invalid choice", unknown.stderr)

    def test_mcp_config_registers_workflow_server(self) -> None:
        mcp_config = json.loads((PLUGIN_ROOT / "mcp" / ".mcp.json").read_text(encoding="utf-8"))

        self.assertEqual(
            mcp_config,
            {
                "mcpServers": {
                    "research-agent-team": {
                        "command": "uv",
                        "args": ["run", "--project", ".", "python", "./scripts/rat_plugin_mcp.py"],
                        "cwd": ".",
                    }
                }
            },
        )

    def test_validation_scripts_pass_from_plugin_root(self) -> None:
        for script_name in ["validate_manifest.py", "validate_schemas.py"]:
            result = subprocess.run(
                [sys.executable, str(PLUGIN_ROOT / "scripts" / script_name)],
                cwd=PLUGIN_ROOT,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("validated", result.stdout.lower())

    def test_png_assets_have_png_magic(self) -> None:
        for relative_path in [
            "assets/icon.png",
            "assets/logo.png",
            "assets/screenshots/project-status.png",
            "assets/screenshots/team-topology.png",
            "assets/screenshots/experiment-review.png",
        ]:
            data = (PLUGIN_ROOT / relative_path).read_bytes()
            self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"), relative_path)


if __name__ == "__main__":
    unittest.main()
