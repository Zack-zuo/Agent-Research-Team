import json
import subprocess
import sys
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


if __name__ == "__main__":
    unittest.main()
