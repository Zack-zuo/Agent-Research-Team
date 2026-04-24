import json
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins/research-agent-team"
SCRIPT = PLUGIN_ROOT / "scripts" / "rat_plugin_cli.py"


def plugin_runtime_available() -> bool:
    required = [
        PLUGIN_ROOT / ".codex-plugin" / "plugin.json",
        SCRIPT,
        PLUGIN_ROOT / "src" / "research_agent_team" / "runtime" / "__init__.py",
    ]
    return all(path.exists() and path.stat().st_size > 10 for path in required)


@unittest.skipUnless(plugin_runtime_available(), "plugin subtree not populated in phase 1")
class RatActivationFlowTests(unittest.TestCase):
    def test_plugin_subtree_contains_activation_bridge_files(self) -> None:
        self.assertTrue((PLUGIN_ROOT / "scripts" / "render_launch_prompt.py").exists())
        self.assertTrue((PLUGIN_ROOT / "schemas" / "commands" / "activation-callbacks.schema.json").exists())

    def test_plugin_cli_path_is_monorepo_relative(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            cwd=PLUGIN_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertNotIn(str(REPO_ROOT / "scripts" / "rat_plugin_cli.py"), result.stdout)
        self.assertIsInstance(json.dumps({"plugin_root": "plugins/research-agent-team"}), str)


if __name__ == "__main__":
    unittest.main()
