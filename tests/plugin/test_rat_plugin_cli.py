import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins/research-agent-team"
SCRIPT = PLUGIN_ROOT / "scripts" / "rat_plugin_cli.py"


def plugin_runtime_available() -> bool:
    required = [
        PLUGIN_ROOT / ".codex-plugin" / "plugin.json",
        SCRIPT,
        PLUGIN_ROOT / "src" / "research_agent_team" / "__init__.py",
    ]
    return all(path.exists() and path.stat().st_size > 10 for path in required)


@unittest.skipUnless(plugin_runtime_available(), "plugin subtree not populated in phase 1")
class RatPluginCliTests(unittest.TestCase):
    def run_cli(self, *args: str, input_text: Optional[str] = None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=PLUGIN_ROOT,
            input=input_text,
            text=True,
            capture_output=True,
            check=True,
        )

    def test_cli_help_is_available_from_plugin_subtree(self) -> None:
        result = self.run_cli("--help")
        self.assertIn("usage:", result.stdout)

    def test_unknown_command_exits_nonzero(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "command", "not_a_command", "--payload-json", json.dumps({})],
            cwd=PLUGIN_ROOT,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_help_and_reserved_later_stage_commands_do_not_import_runtime_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            poison_dir = Path(tmpdir)
            (poison_dir / "yaml.py").write_text(
                "raise RuntimeError('yaml import should be lazy for source CLI help')\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(poison_dir)

            help_result = subprocess.run(
                [sys.executable, str(SCRIPT), "--help"],
                cwd=PLUGIN_ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("usage:", help_result.stdout)

            reserved_result = subprocess.run(
                [sys.executable, str(SCRIPT), "command", "run_experiment", "--payload-json", json.dumps({})],
                cwd=PLUGIN_ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(reserved_result.returncode, 1)
            payload = json.loads(reserved_result.stdout)
            self.assertEqual(payload["error"]["code"], "not_implemented")
            self.assertEqual(payload["error"]["command"], "run_experiment")
            self.assertNotIn("yaml import should be lazy", reserved_result.stderr)

    def test_stage4_and_stage5_commands_are_routed_to_runtime_handlers(self) -> None:
        for command_name in [
            "approve_checkpoint",
            "reject_checkpoint",
            "request_status",
            "generate_report",
            "sync_knowledge_base",
            "rebuild_graph",
        ]:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "command", command_name, "--payload-json", json.dumps({})],
                cwd=PLUGIN_ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertNotEqual(payload["error"]["code"], "not_implemented")


if __name__ == "__main__":
    unittest.main()
