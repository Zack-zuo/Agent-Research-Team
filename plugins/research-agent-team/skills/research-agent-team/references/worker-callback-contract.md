# Worker Callback Contract

Activation callbacks are exposed by the command bridge so workers can report
durable progress without directly mutating supervisor-only state.

Supported callback names:

- `mark-running`
- `heartbeat`
- `checkpoint`
- `complete`
- `fail`

Stage 0 exposes the callback names only. Later activation-runtime stages define
the durable state transitions, bundle materialization, and hard-stop behavior.
