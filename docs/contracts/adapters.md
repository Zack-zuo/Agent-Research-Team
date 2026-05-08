# Adapter Contract

Adapters are optional local integration points used by application services. The v1 plugin ships a graphify-backed graph adapter by default, plus local-file reference implementations for graph fallback and experiment workflows. Missing, disabled, or failing optional adapters must degrade safely and must not corrupt unrelated canonical state.

Adapter configuration lives in `state/adapters/config.json`. Adapter health lives in `state/adapters/health.json` and is normalized during Stage 7 preflight. The normalized shape is:

```json
{
  "graph": {
    "status": "healthy",
    "message": "Graphify graph adapter configured.",
    "updated_at": "2026-05-01T00:00:00Z"
  },
  "experiments": {
    "status": "healthy",
    "message": "Local-file experiment adapter configured.",
    "updated_at": "2026-05-01T00:00:00Z"
  },
  "hooks": {
    "status": "healthy",
    "message": "No hook subscribers configured.",
    "updated_at": "2026-05-01T00:00:00Z"
  }
}
```

Status values should be `healthy`, `degraded`, or `unknown`. Legacy health records using `state` and `last_error` are accepted by preflight and rewritten to the normalized shape.

Graph adapter behavior:

- `rebuild_graph` consumes project-shared wiki artifacts.
- The default graphify adapter writes graph JSON and Markdown report artifacts under `shared/graph/`.
- The legacy `local_file` graph adapter remains available when explicitly configured.
- Disabled or unknown graph adapters return a degraded command result, persist degraded health, and leave unrelated task, activation, and approval state untouched.

Experiment adapter behavior:

- `run_experiment` creates normal task and experiment records before admission.
- The local-file adapter prepares and publishes deterministic filesystem evidence.
- The local-command adapter is opt-in. Select it with `adapter_type: "local_command"` or adapter config, and set `run_parameters.allow_command_execution: true`.
- Local-command runs execute argv-style commands only, with a project-contained working directory, timeout, stdout/stderr logs, runtime metadata, git state/diffs, parsed metrics, and generated outputs saved under `experiments/runs/<experiment-run-id>/`.
- Metrics files may be JSON objects or simple key-value text. Parse failures are recorded in result diagnostics and do not fail publication.
- Disabled or unavailable experiment adapters fail before fabricating successful experiment output.

Worker launch adapter behavior:

- `CodexSubagentLaunchAdapter` is the default managed-execution boundary. It does not wrap Codex or spawn a local process; it returns structured subagent spawn requests for the Codex host and records host-provided handles.
- `FakeSubagentLaunchAdapter` is deterministic test infrastructure. It can simulate spawned, running, completed, failed, cancelled, and stale outcomes while the real execution loop still performs state transitions.
- Worker metadata is stored in each activation directory as `launch-prompt.md`, `worker.json`, and `worker-events.jsonl`. Canonical truth remains in `state/activations`, `state/tasks`, and `state/slots`.
- Real subagents must use the existing activation callback contract. Reconciliation may reflect terminal callback state, recover stale activations, or request host cancellation; it must not fabricate task success without a worker outcome or callback.

New adapters should be introduced behind application-layer ports and must update adapter health with useful operator-facing messages.
