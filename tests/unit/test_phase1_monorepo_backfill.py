import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class Phase1MonorepoBackfillTests(unittest.TestCase):
    def test_root_readmes_are_not_placeholders(self) -> None:
        for relative_path in [
            "README.md",
            "README.zh-CN.md",
        ]:
            path = REPO_ROOT / relative_path
            self.assertTrue(path.exists(), relative_path)
            self.assertGreater(path.stat().st_size, 100, relative_path)

    def test_readme_uses_plugin_subtree_paths(self) -> None:
        readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("plugins/research-agent-team/", readme_text)
        self.assertNotIn("\n.codex-plugin/plugin.json", readme_text)

    def test_repo_metadata_is_portable_and_targets_plugin_root(self) -> None:
        marketplace_path = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"
        workflow_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"

        self.assertGreater(marketplace_path.stat().st_size, 10)
        self.assertGreater(workflow_path.stat().st_size, 10)

        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
        serialized = json.dumps(marketplace)

        self.assertIn("plugins/research-agent-team", serialized)
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn("C:\\", serialized)

    def test_release_scripts_exist(self) -> None:
        for relative_path in [
            "scripts/validate-release-boundary.sh",
            "scripts/package-plugin.sh",
            "scripts/smoke-test-plugin.sh",
        ]:
            path = REPO_ROOT / relative_path
            self.assertTrue(path.exists(), relative_path)
            self.assertGreater(path.stat().st_size, 80, relative_path)

    def test_phase1_tests_are_monorepo_aware(self) -> None:
        for relative_path in [
            "tests/plugin/test_plugin_manifest.py",
            "tests/plugin/test_rat_plugin_cli.py",
            "tests/plugin/test_rat_activation_flow.py",
        ]:
            path = REPO_ROOT / relative_path
            self.assertTrue(path.exists(), relative_path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("plugins/research-agent-team", text, relative_path)

    def test_gitignore_keeps_local_only_paths_untracked(self) -> None:
        gitignore_path = REPO_ROOT / ".gitignore"
        self.assertTrue(gitignore_path.exists())

        text = gitignore_path.read_text(encoding="utf-8")
        self.assertIn("/docs/", text)
        self.assertIn(".env", text)
        self.assertIn(".worktrees/", text)


if __name__ == "__main__":
    unittest.main()
