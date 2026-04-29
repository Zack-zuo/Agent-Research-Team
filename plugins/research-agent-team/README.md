# ResearchAgentTeam Plugin

ResearchAgentTeam is a Codex-first plugin skeleton for local, filesystem-first
research orchestration. The main Codex session acts as the supervisor, while
future senior and junior worker activations will be launched through command
responses and persisted project state.

This package is the installable plugin boundary. Repository docs, tests, CI,
and release tooling remain outside this directory.

## Stage 0 Surface

The Stage 0 package provides:

- plugin metadata in `.codex-plugin/plugin.json`
- supervisor and worker prompt assets under `prompts/`
- the ResearchAgentTeam Codex skill under `skills/`
- baseline command names exposed by `scripts/rat_plugin_cli.py`
- JSON Schema placeholders with valid metadata under `schemas/`
- hook and MCP descriptors that are intentionally inert for Stage 0

Project lifecycle, storage, topology, task admission, activation runtime, and
adapter behavior are implemented in later roadmap stages.

## Local Checks

Run these from the monorepo root:

```bash
bash scripts/validate-release-boundary.sh
bash scripts/smoke-test-plugin.sh
python plugins/research-agent-team/scripts/validate_manifest.py
python plugins/research-agent-team/scripts/validate_schemas.py
```
