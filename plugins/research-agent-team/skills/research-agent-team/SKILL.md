---
name: research-agent-team
description: Use when supervising a ResearchAgentTeam local project, creating or opening a project, assigning work to persistent senior or junior slots, reviewing reports, syncing knowledge, rebuilding graphs, or launching activations returned by ResearchAgentTeam commands.
---

# ResearchAgentTeam Codex Runtime

ResearchAgentTeam is a local-first control-plane plugin. The main Codex session
acts as the supervisor. Deterministic Python services own project bootstrap,
filesystem state, lifecycle commands, and persistent team topology. Task
admission, activation leases, checkpointing, completion, failure, interruption,
cancellation, and stale recovery are owned by the control-plane services.
Stage 4 governance services enforce artifact visibility, write activation
permission manifests, replay approval decisions, generate status/report
artifacts, and rebuild slot inbox/outbox views. Stage 5 services sync
slot-local and project knowledge into Markdown wiki outputs, rebuild local-file
graph exports, and degrade safely when graph adapters are disabled. Stage 6
experiment services run senior-defined, junior-executed experiment tasks,
publish local-file evidence packages, write comparison/review artifacts, and
create follow-up work through the normal task flow.

## MCP Workflow Tools

When Codex exposes this plugin's MCP tools, prefer them over shelling out to
the CLI for supervisor actions:

- `interpret_request`: map natural language to a command plan without
  execution.
- `run_command`: execute a public command and receive an attached
  `launch_plan` when `root_path` is available.
- `activation_callback`: execute activation callbacks and receive launch
  planning for follow-up work.
- `plan_launches`: classify launch requests from prior command or callback
  results.
- `render_launch_prompt`: render an approved launch request into the worker
  prompt.

The MCP tools do not launch Codex workers directly. Use their launch decisions
to decide whether to auto-launch, ask for confirmation, or report an error.

## Command Bridge

From an installed environment, prefer the console command:

```bash
research-agent-team-codex command open_project --payload-json '{"root_path":"/absolute/project"}'
```

From a source checkout, use the plugin-local wrapper:

```bash
python scripts/rat_plugin_cli.py command open_project --payload-json '{"root_path":"/absolute/project"}'
```

Project lifecycle, topology, `assign_task`, experiment, approval decisions,
status, report, knowledge, and graph commands delegate to application services
and return structured JSON.

## Natural-Language Planning

Use MCP `interpret_request` or CLI `interpret` when the user asks in natural
language and you need a safe command plan before execution:

```bash
research-agent-team-codex interpret --root-path "/absolute/project" --text "show current progress"
research-agent-team-codex interpret --root-path "/absolute/project" --text "assign senior-01 a literature review task to review shared/raw"
research-agent-team-codex interpret --root-path "/absolute/project" --text "run a baseline experiment"
```

The interpreter is read-only. It returns `intent`, `confidence`,
`command_name`, `payload`, `missing_fields`, `ambiguous_references`,
`needs_confirmation`, `confirmation_reason`, warnings, validation errors, and
`ready_for_execution`.

Default `conservative` confirmation mode requires confirmation for lifecycle
changes, task assignment, experiments, approvals, and full rebuilds. Use
`--confirmation-mode aggressive` only when the caller explicitly wants clear
non-ambiguous plans to be considered executable immediately.

## Codex Launch Automation

After every successful command or activation callback, inspect the JSON for an
attached `launch_plan` or for `launch_request`, `launch_requests`,
`follow_up_launch_request`, and `next_launch_request`. Do not launch directly
from raw command output. If a launch plan is not already attached, first ask the
runtime to classify the launch work:

```bash
research-agent-team-codex plan-launches --root-path "/absolute/project" --source-command assign_task --payload-file command-result.json
```

Use the default `conservative` policy. Launch decisions mean:

- `auto_launch`: Codex may immediately render the prompt and launch a worker.
- `confirm_launch`: ask the user before launching.
- `error`: do not launch; report the activation/task paths and error.

Conservative auto-launch is limited to clear, non-experiment, non-approval task
activations that are still in `starting` state. Experiments, approval replay,
review follow-ups, long-running or high-budget work, and unclear scopes require
confirmation. If no launch request is returned, explain whether work is queued,
awaiting approval, blocked, or simply not admitted.

## Launch Requests

When a launch decision is approved for launch, render it before creating the
worker. Prefer MCP `render_launch_prompt` when available; otherwise use:

```bash
research-agent-team-codex render-launch-prompt --root-path "/absolute/project" --payload-file launch-request.json
```

The worker must receive only the rendered prompt and allowed bundle context.
Launch Codex workers with isolated context (`fork_context=false` when the
runtime exposes that choice). If prompt rendering fails, preserve the canonical
task and activation state and report the CLI error.

## Worker Discipline

Workers use activation callbacks supplied by the rendered prompt:

```bash
research-agent-team-codex activation mark-running --root-path "/absolute/project" --activation-id activation-id
research-agent-team-codex activation checkpoint --root-path "/absolute/project" --activation-id activation-id --payload-file checkpoint.json
research-agent-team-codex activation complete --root-path "/absolute/project" --activation-id activation-id --payload-file completion.json
research-agent-team-codex activation fail --root-path "/absolute/project" --activation-id activation-id --payload-file failure.json
```

Workers must mark the activation running before doing work, then finish through
`complete`, `fail`, `interrupt`, or a durable `checkpoint` when useful. If a
worker returns a result without making a terminal callback, call `fail` with a
concrete failure summary instead of leaving the activation in limbo. After a
terminal callback, inspect the callback result for `next_launch_request` and
repeat the launch automation flow. Treat project files and command JSON as the
source of truth.
