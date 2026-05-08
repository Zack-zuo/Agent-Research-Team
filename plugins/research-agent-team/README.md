# ResearchAgentTeam Plugin

ResearchAgentTeam is a Codex-first plugin for local, filesystem-first research
orchestration. The main Codex session acts as the supervisor, while senior and
junior slots are persisted in project state and later activated through command
responses.

This package is the installable plugin boundary. Repository docs, tests, CI,
and release tooling remain outside this directory.

## Implemented Surface

The package provides:

- plugin metadata in `.codex-plugin/plugin.json`
- supervisor and worker prompt assets under `prompts/`
- the ResearchAgentTeam Codex skill under `skills/`
- project lifecycle commands for `create_project`, `open_project`, mode
  switching, pause, and resume
- topology commands for supervisor, senior, and junior slot inspection and
  staffing mutations
- task admission through `assign_task`, including queue placement, activation
  materialization, launch requests, and command-driven advancement
- activation runtime callbacks for running, heartbeat, checkpoint, completion,
  failure, interruption, cancellation, and stale recovery on project preflight
- Stage 4 governance commands for approving or rejecting checkpoints,
  requesting status, and generating reports
- artifact indexing, visibility enforcement, activation permission manifests,
  budget warnings, durable report artifacts, and derived slot inbox/outbox views
- Stage 5 knowledge commands for syncing slot/project wiki outputs and
  rebuilding graphify-backed graph exports under `shared/graph/`
- Stage 6 experiment commands for senior-defined, junior-executed experiment
  requests, local-file run publication, opt-in local command execution with
  logs/metrics/git capture, comparison outputs, review artifacts, and follow-up
  task creation
- Stage 7 hardening for command preflight, supported schema migration, adapter
  health normalization, best-effort hook delivery, integrity validation, and
  release packaging checks
- deterministic natural-language interpretation through `interpret`, which
  maps user requests to structured command plans without executing them
- Codex-side launch planning through `plan-launches`, which classifies command
  `launch_request` results before Codex renders prompts and spawns workers
- managed execution-loop commands that store activation prompts and worker
  metadata while returning Codex subagent spawn requests to the host
- a local STDIO MCP server exposing workflow tools for interpretation,
  command execution with launch planning, activation callbacks with follow-up
  planning, standalone launch planning, launch prompt rendering, and managed
  execution
- deterministic domain, storage, config, and shared helpers for the project
  filesystem contract
- JSON Schema coverage for command, state, event, adapter, and manifest shapes
- hook descriptors and MCP registration metadata

## Local Checks

Run these from the monorepo root:

```bash
bash scripts/validate-release-boundary.sh
bash scripts/smoke-test-plugin.sh
bash scripts/package-plugin.sh
python plugins/research-agent-team/scripts/validate_manifest.py
python plugins/research-agent-team/scripts/validate_schemas.py
```

## Natural-Language Interpretation

Use `interpret` to turn a user request into a validated command plan before
deciding whether to execute it:

```bash
research-agent-team-codex interpret \
  --root-path "/absolute/project" \
  --text "assign senior-01 a literature review task to review shared/raw"
```

The result includes `intent`, `command_name`, `payload`, `missing_fields`,
`ambiguous_references`, `needs_confirmation`, `confirmation_reason`, warnings,
validation errors, and `ready_for_execution`. Interpretation is read-only; run
the returned command separately after confirmation.

The default confirmation mode is conservative. Clear open/status requests can
proceed, while lifecycle changes, task assignment, experiments, approvals, and
high-impact full rebuilds require confirmation. Use
`--confirmation-mode aggressive` when the caller wants confirmation only for
missing, ambiguous, or invalid plans.

## MCP Workflow Surface

Codex loads the plugin MCP server from `mcp/.mcp.json`. The server runs over
STDIO and exposes workflow tools including:

- `interpret_request`
- `run_command`
- `activation_callback`
- `plan_launches`
- `render_launch_prompt`
- `execution_plan_pending`
- `execution_start_pending`
- `execution_attach_subagent`
- `execution_inspect`
- `execution_cancel`
- `execution_reconcile`

`run_command` and `activation_callback` automatically attach a conservative
`launch_plan` when a usable project `root_path` is available. The execution
tools render activation prompts, write `worker.json` and `worker-events.jsonl`
under the activation directory, and return structured Codex subagent spawn
requests. Codex remains the host: the supervisor spawns the subagent, then calls
`execution_attach_subagent` with the host session handle. Managed start preserves
launch confirmation gates and starts only `auto_launch` decisions.

For source checkouts, the MCP server can be started directly:

```bash
uv run --project . python ./scripts/rat_plugin_mcp.py
```

The CLI bridge remains available as a fallback and for worker callback commands
embedded in rendered activation prompts.

Managed execution can be driven from the CLI:

```bash
research-agent-team-codex execution start-pending \
  --root-path "/absolute/path/to/project" \
  --adapter codex-subagent \
  --max-concurrent 1

research-agent-team-codex execution attach-subagent \
  --root-path "/absolute/path/to/project" \
  --activation-id activation-id \
  --handle codex-session-id

research-agent-team-codex execution reconcile \
  --root-path "/absolute/path/to/project"
```

Use `--adapter fake-subagent` with fake launch/observe/cancel statuses for
deterministic tests and smoke runs.

## Local Command Experiments

The default experiment adapter remains `local_file`. To run a real local
command, pass `adapter_type: "local_command"` to `run_experiment` and include an
explicit safety opt-in:

```json
{
  "adapter_type": "local_command",
  "run_parameters": {
    "allow_command_execution": true,
    "working_directory": "shared/raw",
    "timeout_seconds": 300,
    "command": ["python", "scripts/baseline.py"],
    "metrics_files": ["metrics.json"],
    "output_paths": ["results.json"]
  }
}
```

Commands are executed without a shell, inside the project root, and never under
`state/`. Publication stores stdout/stderr logs, execution metadata,
environment metadata, git state/diffs, generated outputs, metrics files, and
the result record under `experiments/runs/<experiment-run-id>/`, then indexes
those artifacts as project-shared evidence. JSON metrics files must contain an
object; text metrics support `key=value` or `key: value`. Metrics parse errors
are recorded in diagnostics and do not block publication.
