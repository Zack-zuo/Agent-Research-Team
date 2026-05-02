# Worker Callback Contract

Activation callbacks are exposed by MCP `activation_callback` and the CLI
command bridge so workers can report durable progress without directly mutating
supervisor-only state.

Supported callback names:

- `mark-running`
- `heartbeat`
- `checkpoint`
- `complete`
- `fail`
- `interrupt`
- `cancel`

Activation inputs include `bundle.json`, `briefing.md`, `runtime.json`, and
`permissions.json`. The permission manifest is the worker's explicit list of
allowed artifact IDs, path roots, and grants.

The supervisor must use the attached MCP `launch_plan` or run `plan-launches`
before rendering a prompt. Workers should never receive raw supervisor context
or unclassified launch requests. They receive only the rendered activation
prompt and the bundle context embedded in that prompt.

The runtime persists durable state transitions, bundle materialization,
checkpoints, budget heartbeats, terminal activation outcomes, artifact indexes,
and stale activation recovery.
