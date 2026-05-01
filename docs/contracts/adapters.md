# Adapter Contract

Adapters are optional local integration points used by application services. The v1 plugin ships local-file reference implementations for graph and experiment workflows. Missing, disabled, or failing optional adapters must degrade safely and must not corrupt unrelated canonical state.

Adapter configuration lives in `state/adapters/config.json`. Adapter health lives in `state/adapters/health.json` and is normalized during Stage 7 preflight. The normalized shape is:

```json
{
  "graph": {
    "status": "healthy",
    "message": "Local-file graph adapter configured.",
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
- The local-file adapter writes graph JSON and Markdown report artifacts under `shared/graph/`.
- Disabled or unknown graph adapters return a degraded command result, persist degraded health, and leave unrelated task, activation, and approval state untouched.

Experiment adapter behavior:

- `run_experiment` creates normal task and experiment records before admission.
- The local-file adapter prepares and publishes deterministic filesystem evidence.
- Disabled or unavailable experiment adapters fail before fabricating successful experiment output.

New adapters should be introduced behind application-layer ports and must update adapter health with useful operator-facing messages.
