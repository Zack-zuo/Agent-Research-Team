# Command Contract

ResearchAgentTeam commands are exposed through both the plugin MCP server and `research-agent-team-codex command <name>`. Both adapters return one JSON object with `ok` plus either `result` or `error`. Command payloads are JSON objects supplied as MCP tool arguments, `--payload-json`, `--payload-file`, or stdin. The protocol adapters are intentionally thin: they validate transport shape, load the matching application service, and serialize structured errors without duplicating business rules.

Natural-language interpretation is exposed through `research-agent-team-codex interpret --text "<request>"`. It is a read-only planning surface, not a state transition. The interpreter returns a structured command plan with `intent`, `confidence`, `command_name`, `payload`, `missing_fields`, `ambiguous_references`, `resolved_references`, `needs_confirmation`, `confirmation_reason`, `warnings`, `validation_errors`, and `ready_for_execution`. Callers must dispatch the planned command separately after applying their confirmation policy.

Codex-side launch planning is exposed through the MCP `plan_launches` tool and `research-agent-team-codex plan-launches`. It accepts a prior command or activation callback result and returns launch decisions without mutating canonical project state. The default `conservative` policy permits automatic launch only for clear, non-experiment, non-approval activations that still match starting activation, admitted task, owner slot, and materialized bundle state. Experiments, approval replay, review follow-ups, high-budget or long-running work, unclear scopes, invalid state, and stale activations return confirmation or error decisions instead.

Managed execution is exposed through the MCP `execution_*` tools and `research-agent-team-codex execution <subcommand>`. Codex is the host and the plugin runs inside it: `execution_start_pending` renders launch prompts, writes worker metadata, and returns `subagent_launch_requests` for the Codex supervisor to spawn. The supervisor records the returned host handle with `execution_attach_subagent`. Worker callbacks remain the canonical way for subagents to mark running, heartbeat, checkpoint, complete, or fail.

All state-sensitive commands except `create_project` run the Stage 7 preflight under the project lock before their main transition. Preflight may migrate a supported schema, repair support surfaces, normalize adapter health, validate integrity, and return warnings. Warnings are non-fatal and must be preserved in the command result. Fatal preflight failures return `ok: false` with a stable `error.code`.

Public project commands:

- `create_project`: creates a new project root, state layout, supervisor slot, default senior slot, policies, budgets, adapter health, hook config, and initial charter artifact.
- `open_project`: prepares an existing project, runs stale recovery, rebuilds derived slot views, and returns project summary, topology, recovery counts, warnings, adapter health, migration flags, work counts, approvals, and recent artifacts.
- `switch_operating_mode`, `pause_project`, `resume_project`: update project lifecycle state after preflight. `resume_project` may admit queued work and return launch requests.

Public topology commands:

- `show_team_topology`: returns the current topology after preflight.
- `add_senior`, `add_junior`, `retire_senior`, `retire_junior`: mutate persistent slot topology under staffing policy and approval rules.

Public workflow commands:

- `assign_task`: validates requester, owner, visibility, paths, budget, and admission. It may return a `launch_request`.
- `approve_checkpoint`, `reject_checkpoint`: decide pending approvals and replay approved side effects.
- `request_status`, `generate_report`: write durable Markdown report artifacts and index them.
- `sync_knowledge_base`, `rebuild_graph`: compile knowledge and build local graph outputs, with degraded graph behavior when configured adapters are unavailable.
- `run_experiment`, `review_experiment`: create experiment work through normal task admission and review produced evidence.

Common interpretation examples:

- `open this project` -> `open_project`
- `show current progress` -> `request_status`
- `assign senior-01 a literature review task` -> `assign_task`
- `pause the project` -> `pause_project`
- `resume from the last checkpoint` -> `resume_project`
- `sync the knowledge base` -> `sync_knowledge_base`
- `rebuild the graph from scratch` -> `rebuild_graph` with `mode=full`
- `run a baseline experiment` -> `run_experiment`

Activation callbacks use the MCP `activation_callback` tool or `research-agent-team-codex activation <callback>` and are scoped by root path plus activation id. Supported callbacks are `mark-running`, `heartbeat`, `checkpoint`, `complete`, `fail`, `interrupt`, and `cancel`. Callbacks update canonical activation, task, checkpoint, budget, artifact, experiment, and event state as appropriate.

MCP workflow tools:

- `interpret_request`: maps natural-language requests to command plans.
- `run_command`: runs public commands and includes `launch_plan` when `root_path` is available.
- `activation_callback`: runs activation callbacks and includes `launch_plan` for follow-up work.
- `plan_launches`: classifies launch requests from prior results.
- `render_launch_prompt`: renders an approved launch request into a worker prompt.
- `execution_plan_pending`: inspects pending activation launches and concurrency capacity.
- `execution_start_pending`: renders prompts, persists worker state, and returns Codex subagent spawn requests.
- `execution_attach_subagent`: records a Codex host subagent/session handle.
- `execution_inspect`, `execution_cancel`, `execution_reconcile`: operate on managed worker records.

Launch handling flow:

1. Run a command or callback through MCP or the CLI bridge.
2. Use the attached MCP `launch_plan`, or pass the full JSON result to `plan-launches`.
3. For `auto_launch`, prefer `execution_start_pending`; it stores `launch-prompt.md`, reserves `worker.json`, writes `worker-events.jsonl`, then returns Codex subagent spawn requests.
4. For `confirm_launch`, ask the user before rendering and launching.
5. After the Codex host spawns a subagent, call `execution_attach_subagent` with the host handle.
6. For `error`, leave canonical state untouched and report the activation, task, and slot state paths.
7. After worker completion or failure callbacks, inspect any `next_launch_request` and repeat the same flow.
8. Use `execution_reconcile` to mark stale worker metadata and reflect terminal activation state.

`execution_start_pending` must not bypass confirmation decisions. It starts only pending launches classified as `auto_launch`; all confirmation, error, and blocked decisions are returned as blocked launch records. Worker reservation is written while holding the project lock so concurrent supervisors cannot receive duplicate spawn requests for the same activation.
