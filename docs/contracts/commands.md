# Command Contract

ResearchAgentTeam commands are exposed through `research-agent-team-codex command <name>` and return one JSON object with `ok` plus either `result` or `error`. Command payloads are JSON objects supplied by `--payload-json`, `--payload-file`, or stdin. The command bridge is intentionally thin: it validates transport shape, loads the matching application service, and serializes structured errors without duplicating business rules.

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

Activation callbacks use `research-agent-team-codex activation <callback>` and are scoped by root path plus activation id. Supported callbacks are `mark-running`, `heartbeat`, `checkpoint`, `complete`, `fail`, `interrupt`, and `cancel`. Callbacks update canonical activation, task, checkpoint, budget, artifact, experiment, and event state as appropriate.
