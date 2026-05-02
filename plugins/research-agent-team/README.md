# ResearchAgentTeam Plugin

ResearchAgentTeam is a Codex-first plugin for local, filesystem-first research
orchestration. The main Codex session acts as the supervisor, while senior and
junior slots are persisted in project state and later activated through command
responses.

This package is the installable plugin boundary. Repository docs, tests, CI,
and release tooling remain outside this directory.

## Implemented Surface

The package provides:

- plugin metadata in `.codex-plugin/plugin.json`
- supervisor and worker prompt assets under `prompts/`
- the ResearchAgentTeam Codex skill under `skills/`
- project lifecycle commands for `create_project`, `open_project`, mode
  switching, pause, and resume
- topology commands for supervisor, senior, and junior slot inspection and
  staffing mutations
- task admission through `assign_task`, including queue placement, activation
  materialization, launch requests, and command-driven advancement
- activation runtime callbacks for running, heartbeat, checkpoint, completion,
  failure, interruption, cancellation, and stale recovery on project preflight
- Stage 4 governance commands for approving or rejecting checkpoints,
  requesting status, and generating reports
- artifact indexing, visibility enforcement, activation permission manifests,
  budget warnings, durable report artifacts, and derived slot inbox/outbox views
- Stage 5 knowledge commands for syncing slot/project wiki outputs and
  rebuilding graphify-backed graph exports under `shared/graph/`
- Stage 6 experiment commands for senior-defined, junior-executed experiment
  requests, local-file run publication, comparison outputs, review artifacts,
  and follow-up task creation
- Stage 7 hardening for command preflight, supported schema migration, adapter
  health normalization, best-effort hook delivery, integrity validation, and
  release packaging checks
- deterministic domain, storage, config, and shared helpers for the project
  filesystem contract
- JSON Schema coverage for command, state, event, adapter, and manifest shapes
- hook and MCP descriptors; MCP registration remains optional and inert until a
  later roadmap stage

## Local Checks

Run these from the monorepo root:

```bash
bash scripts/validate-release-boundary.sh
bash scripts/smoke-test-plugin.sh
bash scripts/package-plugin.sh
python plugins/research-agent-team/scripts/validate_manifest.py
python plugins/research-agent-team/scripts/validate_schemas.py
```
