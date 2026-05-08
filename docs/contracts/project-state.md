# Project State Contract

A generated ResearchAgentTeam project root is runtime output. It is separate from the source monorepo and separate from the installable plugin package. The source of truth for coordination lives under `state/`; human-facing work products live under `agents/`, `shared/`, and `experiments/`.

Required top-level paths:

- `project.yaml`: human-readable manifest with project id, name, schema version, root path, status, operating mode, and supervisor slot id.
- `state/project.json`: canonical project entity.
- `state/topology/current.json`: canonical team topology.
- `state/slots/*.json`: persistent supervisor, senior, and junior slot records.
- `state/tasks/*.json`, `state/activations/*.json`, `state/approvals/*.json`: orchestration state.
- `state/budgets/current.json`, `state/policies/*.json`: budget and policy configuration.
- `state/adapters/config.json`, `state/adapters/health.json`: adapter configuration and normalized health.
- `state/knowledge/`, `state/experiments/`, `state/migrations/`, `state/hooks/`: knowledge, experiment, migration, and hook support state.
- `state/events/*.jsonl` and `state/artifacts/index.jsonl`: append-only audit surfaces.

Managed execution adds per-activation worker files under `agents/<slot-id>/activations/<activation-id>/`:

- `launch-prompt.md`: rendered prompt handed to the Codex subagent.
- `worker.json`: plugin-managed worker metadata such as adapter, status, host handle, prompt path, timeout, timestamps, cancellation reason, and diagnostics.
- `worker-events.jsonl`: append-only worker lifecycle log for launch, attach, observation, cancellation, and reconciliation events.

These worker files are operational metadata. Canonical lifecycle truth remains in `state/activations/*.json`, `state/tasks/*.json`, and `state/slots/*.json`.

Stage 7 preflight may recreate support or derived surfaces when they are missing. Repairable surfaces include state directories, hook configuration, hook logs, daily event and message logs, artifact index files, slot inbox/outbox folders, and latest aliases for timestamped report or graph outputs. Preflight must not fabricate missing canonical business entities such as tasks, slots, activations, approvals, experiment requests, runs, comparisons, or reviews.

Integrity validation checks that references across canonical entities point to existing records. It rejects tasks with missing owners, activations with missing slots or tasks, slots with missing current activations, multiple active activations for one slot, malformed experiment references, invalid approval targets, and artifact paths that escape the project root.

The current schema version is `0.2.0`. Automatic migration is supported only from `0.1.0` to `0.2.0`. Unsupported versions fail before normal command behavior continues.
