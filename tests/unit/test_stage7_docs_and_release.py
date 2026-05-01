import json
import unittest
from pathlib import Path

import tomllib


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "research-agent-team"


class Stage7DocsAndReleaseTests(unittest.TestCase):
    def test_stage7_contract_docs_and_runbooks_are_populated(self) -> None:
        required_docs = [
            "docs/contracts/commands.md",
            "docs/contracts/project-state.md",
            "docs/contracts/events.md",
            "docs/contracts/hooks.md",
            "docs/contracts/adapters.md",
            "docs/runbooks/activation-failures.md",
            "docs/runbooks/release-checklist.md",
        ]
        for relative_path in required_docs:
            text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            self.assertGreater(len(text.strip()), 500, relative_path)
            self.assertNotIn("TBD", text)
            self.assertNotIn("TODO", text)

    def test_m8_signature_versions_are_aligned(self) -> None:
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        pyproject = tomllib.loads((PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        init_text = (PLUGIN_ROOT / "src" / "research_agent_team" / "__init__.py").read_text(encoding="utf-8")

        self.assertIn("## M8 - Stage 7", changelog)
        self.assertEqual(manifest["version"], "0.2.0")
        self.assertEqual(pyproject["project"]["version"], "0.2.0")
        self.assertIn('__version__ = "0.2.0"', init_text)


if __name__ == "__main__":
    unittest.main()
