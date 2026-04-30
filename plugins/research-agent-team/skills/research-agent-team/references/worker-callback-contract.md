# Worker Callback Contract

Activation callbacks are exposed by the command bridge so workers can report
durable progress without directly mutating supervisor-only state.

Supported callback names:

- `mark-running`
- `heartbeat`
- `checkpoint`
- `complete`
- `fail`
- `interrupt`
- `cancel`

Activation inputs include `bundle.json`, `briefing.md`, and `runtime.json`.

The runtime persists durable state transitions, bundle materialization,
checkpoints, budget heartbeats, terminal activation outcomes, basic artifact
indexes, and stale activation recovery.
