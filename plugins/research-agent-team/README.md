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
- deterministic natural-language interpretation through `interpret`, which
  maps user requests to structured command plans without executing them
- Codex-side launch planning through `plan-launches`, which classifies command
  `launch_request` results before Codex renders prompts and spawns workers
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

## Natural-Language Interpretation

Use `interpret` to turn a user request into a validated command plan before
deciding whether to execute it:

```bash
research-agent-team-codex interpret \
  --root-path "/absolute/project" \
  --text "assign senior-01 a literature review task to review shared/raw"
```

The result includes `intent`, `command_name`, `payload`, `missing_fields`,
`ambiguous_references`, `needs_confirmation`, `confirmation_reason`, warnings,
validation errors, and `ready_for_execution`. Interpretation is read-only; run
the returned command separately after confirmation.

The default confirmation mode is conservative. Clear open/status requests can
proceed, while lifecycle changes, task assignment, experiments, approvals, and
high-impact full rebuilds require confirmation. Use
`--confirmation-mode aggressive` when the caller wants confirmation only for
missing, ambiguous, or invalid plans.
