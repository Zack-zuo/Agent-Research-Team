import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins/research-agent-team"


def plugin_runtime_available() -> bool:
    required = [
        PLUGIN_ROOT / ".codex-plugin" / "plugin.json",
        PLUGIN_ROOT / "scripts" / "rat_plugin_cli.py",
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
        self.assertEqual(manifest["interface"]["displayName"], "ResearchAgentTeam")
        self.assertNotIn("mcpServers", manifest)

    def test_plugin_assets_are_relative_to_plugin_subtree(self) -> None:
        self.assertTrue((PLUGIN_ROOT / "skills" / "research-agent-team" / "SKILL.md").exists())
        self.assertTrue((PLUGIN_ROOT / "scripts" / "rat_plugin_cli.py").exists())
        self.assertFalse((PLUGIN_ROOT / ".agents" / "plugins" / "marketplace.json").exists())


if __name__ == "__main__":
    unittest.main()
