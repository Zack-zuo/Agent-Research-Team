import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins/research-agent-team"


def plugin_runtime_available() -> bool:
    required = [
        PLUGIN_ROOT / ".codex-plugin" / "plugin.json",
        PLUGIN_ROOT / "scripts" / "rat_plugin_cli.py",
        PLUGIN_ROOT / "scripts" / "rat_plugin_mcp.py",
        PLUGIN_ROOT / "mcp" / ".mcp.json",
        PLUGIN_ROOT / "skills" / "research-agent-team" / "SKILL.md",
    ]
    return all(path.exists() and path.stat().st_size > 10 for path in required)


@unittest.skipUnless(plugin_runtime_available(), "plugin subtree not populated in phase 1")
class PluginManifestTests(unittest.TestCase):
    def test_plugin_manifest_lives_at_plugin_subtree_root(self) -> None:
        manifest_path = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["name"], "research-agent-team")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["mcpServers"], "./mcp/.mcp.json")
        self.assertEqual(manifest["interface"]["displayName"], "ResearchAgentTeam")

    def test_plugin_assets_are_relative_to_plugin_subtree(self) -> None:
        self.assertTrue((PLUGIN_ROOT / "skills" / "research-agent-team" / "SKILL.md").exists())
        self.assertTrue((PLUGIN_ROOT / "scripts" / "rat_plugin_cli.py").exists())
        self.assertTrue((PLUGIN_ROOT / "scripts" / "rat_plugin_mcp.py").exists())
        self.assertFalse((PLUGIN_ROOT / ".agents" / "plugins" / "marketplace.json").exists())

    def test_mcp_config_registers_local_stdio_server(self) -> None:
        mcp_config = json.loads((PLUGIN_ROOT / "mcp" / ".mcp.json").read_text(encoding="utf-8"))

        server = mcp_config["mcpServers"]["research-agent-team"]
        self.assertEqual(server["command"], "uv")
        self.assertEqual(server["args"], ["run", "--project", ".", "python", "./scripts/rat_plugin_mcp.py"])
        self.assertEqual(server["cwd"], ".")


if __name__ == "__main__":
    unittest.main()
