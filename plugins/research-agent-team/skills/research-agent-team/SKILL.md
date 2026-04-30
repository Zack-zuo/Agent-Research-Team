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
artifacts, and rebuild slot inbox/outbox views. Knowledge, graph, and experiment
workflows are added by later roadmap stages.

## Command Bridge

From an installed environment, prefer the console command:

```bash
research-agent-team-codex command open_project --payload-json '{"root_path":"/absolute/project"}'
```

From a source checkout, use the plugin-local wrapper:

```bash
python scripts/rat_plugin_cli.py command open_project --payload-json '{"root_path":"/absolute/project"}'
```

Project lifecycle, topology, `assign_task`, approval decisions, status, and
report commands delegate to application services and return structured JSON.
Knowledge, graph, and experiment commands still return structured
`not_implemented` responses until their roadmap stages attach business services.

## Launch Requests

When `assign_task` returns a non-null `launch_request`, render it before
launching a worker:

```bash
research-agent-team-codex render-launch-prompt --root-path "/absolute/project" --payload-file launch-request.json
```

The worker must receive only the rendered prompt and allowed bundle context.

## Worker Discipline

Workers use activation callbacks supplied by the rendered prompt:

```bash
research-agent-team-codex activation mark-running --root-path "/absolute/project" --activation-id activation-id
research-agent-team-codex activation checkpoint --root-path "/absolute/project" --activation-id activation-id --payload-file checkpoint.json
research-agent-team-codex activation complete --root-path "/absolute/project" --activation-id activation-id --payload-file completion.json
research-agent-team-codex activation fail --root-path "/absolute/project" --activation-id activation-id --payload-file failure.json
```

Treat project files and command JSON as the source of truth.
