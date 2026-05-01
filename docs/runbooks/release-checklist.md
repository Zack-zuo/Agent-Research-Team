# Release Checklist

This checklist validates the M8 Stage 7 release boundary for ResearchAgentTeam. Run all commands from the monorepo root.

## Required checks

```bash
uv run pytest
bash scripts/validate-release-boundary.sh
bash scripts/smoke-test-plugin.sh
bash scripts/package-plugin.sh
```

Expected results:

- pytest exits with zero failures
- manifest validation succeeds
- schema validation succeeds
- plugin boundary validation succeeds
- CLI help works from the plugin subtree
- `dist/research-agent-team-plugin.tar.gz` is created from `plugins/research-agent-team/`

## Boundary checks

The installable package is exactly `plugins/research-agent-team/`. Release artifacts must not include monorepo-only material such as root `docs/`, root `tests/`, `.github/`, `.agents/`, root changelog, or root release scripts. The plugin may include its own README, scripts, schemas, prompts, skills, assets, hooks, MCP descriptors, source package, license, and package metadata.

## Version checks

For M8, these values must match:

- `plugins/research-agent-team/.codex-plugin/plugin.json`: `version`
- `plugins/research-agent-team/pyproject.toml`: project version
- `plugins/research-agent-team/src/research_agent_team/__init__.py`: `__version__`
- `CHANGELOG.md`: `## M8 - Stage 7`

If schema changes are introduced, update migration docs and add a supported migration path before release. Unsupported schema versions must continue to fail fast.

## Smoke scenario

Before publishing, create a fresh local project, open it, assign a task, render a launch prompt, complete the activation, request status, sync knowledge, rebuild the graph, generate a final package, and reopen the project without integrity warnings. Hook subscribers may be disabled for this scenario unless hook behavior changed.
