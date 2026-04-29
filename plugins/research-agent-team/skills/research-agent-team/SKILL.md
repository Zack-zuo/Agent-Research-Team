---
name: research-agent-team
description: Use when supervising a ResearchAgentTeam local project, creating or opening a project, assigning work to persistent senior or junior slots, reviewing reports, syncing knowledge, rebuilding graphs, or launching activations returned by ResearchAgentTeam commands.
---

# ResearchAgentTeam Codex Runtime

ResearchAgentTeam is a local-first control-plane plugin. The main Codex session
acts as the supervisor. Deterministic Python services will own durable state,
task admission, activation leases, budgets, artifacts, and reports as later
roadmap stages are implemented.

## Command Bridge

From an installed environment, prefer the console command:

```bash
research-agent-team-codex command open_project --payload-json '{"root_path":"/absolute/project"}'
```

From a source checkout, use the plugin-local wrapper:

```bash
python scripts/rat_plugin_cli.py command open_project --payload-json '{"root_path":"/absolute/project"}'
```

Stage 0 exposes the command names and returns structured `not_implemented`
responses for business commands. Later stages attach these names to application
services.

## Launch Requests

When a future command returns a non-null `launch_request`, render it before
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
