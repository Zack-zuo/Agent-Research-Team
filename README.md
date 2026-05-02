# ResearchAgentTeam

[中文说明](./README.zh-CN.md)

ResearchAgentTeam is a Codex-first, local-first research orchestration plugin. It helps a researcher keep a long-running computational project alive across many sessions by storing team structure, task flow, activation state, reports, experiment evidence, and reusable knowledge on disk.

The main Codex session acts as the `supervisor`. Persistent `senior_phd` and `junior_phd` slots are stored in the project state and activated through explicit commands. Instead of relying on chat history as the only memory, ResearchAgentTeam makes project state inspectable, recoverable, and reusable through files.

## What It Gives You

- Persistent team memory: stable supervisor, senior, and junior slots with durable state.
- Filesystem-first outputs: reports, notes, graph summaries, experiment evidence, and wiki pages stay in the project root.
- Explicit task flow: tasks are assigned, admitted, launched, checkpointed, completed, failed, or reviewed through command results.
- Safe Codex launch handling: launch requests are classified before a worker prompt is rendered.
- Knowledge accumulation: shared materials can be compiled into reusable wiki-style outputs.
- Experiment workflow: senior-defined, junior-executed experiment runs produce evidence packages and review records.
- Operator visibility: status and final package reports summarize progress, approvals, warnings, and next work.

## Repository Layout

This repository is a source monorepo. The installable Codex plugin is the subtree under `plugins/research-agent-team/`; root-level docs, tests, and release scripts are development assets.

```text
research-agent-team/
├── README.md
├── README.zh-CN.md
├── docs/
├── scripts/
├── tests/
└── plugins/
    └── research-agent-team/
        ├── .codex-plugin/plugin.json
        ├── mcp/.mcp.json
        ├── skills/research-agent-team/SKILL.md
        ├── scripts/rat_plugin_cli.py
        ├── scripts/rat_plugin_mcp.py
        ├── schemas/
        └── src/research_agent_team/
```

When you create a research project, the runtime creates a separate project root like this:

```text
MyResearchProject/
├── project.yaml
├── state/
├── agents/
├── shared/
├── experiments/
└── logs/
```

Important generated paths:

- `project.yaml`: human-readable project manifest.
- `state/project.json`: canonical project record.
- `state/topology/current.json`: active team topology.
- `state/slots/*.json`: supervisor, senior, and junior slot records.
- `state/tasks/*.json`: task records.
- `state/activations/*.json`: worker activation records.
- `state/approvals/*.json`: pending and resolved approvals.
- `state/artifacts/index.jsonl`: append-only artifact index.
- `shared/reports/status-latest.md`: latest status report.
- `shared/reports/final-package-latest.md`: latest final package report.
- `shared/wiki/`: compiled knowledge pages.
- `shared/graph/`: graph JSON and Markdown summaries.
- `experiments/runs/`: experiment run evidence and publish manifests.
- `logs/hooks/YYYY-MM-DD.jsonl`: local hook delivery diagnostics.

## Install And Verify

ResearchAgentTeam requires Python 3.12 or newer.

Install the plugin package from the monorepo root:

```bash
python -m pip install -e plugins/research-agent-team
```

Verify the installed command bridge:

```bash
research-agent-team-codex --help
research-agent-team-codex command --help
```

From a source checkout, you can use the plugin-local CLI wrapper without relying on the installed console script:

```bash
python plugins/research-agent-team/scripts/rat_plugin_cli.py --help
python plugins/research-agent-team/scripts/rat_plugin_cli.py command --help
```

The installable plugin metadata lives at:

```text
plugins/research-agent-team/.codex-plugin/plugin.json
```

The Codex skill that explains how to supervise a project lives at:

```text
plugins/research-agent-team/skills/research-agent-team/SKILL.md
```

The plugin also registers a local STDIO MCP server through:

```text
plugins/research-agent-team/mcp/.mcp.json
```

For a source checkout, start the MCP server from the plugin root:

```bash
cd plugins/research-agent-team
uv run --project . python ./scripts/rat_plugin_mcp.py
```

## Recommended Codex Workflow

When the MCP tools are available, Codex should use them before shelling out to the CLI:

- `interpret_request`: map natural language to a command plan without changing state.
- `run_command`: run a public project command and attach a conservative `launch_plan` when `root_path` is available.
- `activation_callback`: update activation state and attach launch planning for follow-up work.
- `plan_launches`: classify launch requests from a previous command or callback result.
- `render_launch_prompt`: render an approved launch request into a worker prompt.

The MCP tools do not spawn workers directly. They return enough structure for Codex to decide whether to auto-launch, ask for confirmation, or report an error.

Use the CLI bridge when MCP tools are unavailable or when you are operating from a terminal:

```bash
research-agent-team-codex command <command_name> --payload-json '{...}'
research-agent-team-codex command <command_name> --payload-file payload.json
printf '{"root_path":"/absolute/project"}' | research-agent-team-codex command open_project
```

## End-To-End Tutorial

The examples below use an absolute project path. Replace it with your own path:

```bash
PROJECT_ROOT="/absolute/path/to/my-research-project"
```

### 1. Create A Project

`create_project` creates a project root, state layout, supervisor slot, default `senior-01` slot, policies, budgets, adapter health, hook config, and initial charter artifact. The target root must be missing or empty.

```bash
research-agent-team-codex command create_project --payload-json '{
  "name": "Demo Research",
  "root_path": "/absolute/path/to/my-research-project",
  "initial_charter_text": "# Demo Research\n\nInitial scope and research questions.\n"
}'
```

Expected command envelope:

```json
{
  "ok": true,
  "result": {
    "project": "...",
    "topology": "...",
    "warnings": []
  }
}
```

If you are using MCP, call `run_command` with:

```json
{
  "command_name": "create_project",
  "payload": {
    "name": "Demo Research",
    "root_path": "/absolute/path/to/my-research-project",
    "initial_charter_text": "# Demo Research\n\nInitial scope and research questions.\n"
  }
}
```

### 2. Open Or Resume A Project

Run `open_project` whenever you return to an existing project. It runs preflight under the project lock, performs supported migration, repairs support surfaces, normalizes adapter health, recovers stale activations, rebuilds derived views, and returns a fresh operator snapshot.

```bash
research-agent-team-codex command open_project --payload-json '{
  "root_path": "/absolute/path/to/my-research-project"
}'
```

Use this result first before inspecting raw JSON files. Warnings are usually non-fatal; fatal integrity errors return `ok: false` with a stable `error.code`.

### 3. Inspect Or Change The Team

Show current topology:

```bash
research-agent-team-codex command show_team_topology --payload-json '{
  "root_path": "/absolute/path/to/my-research-project"
}'
```

Add a senior slot:

```bash
research-agent-team-codex command add_senior --payload-json '{
  "root_path": "/absolute/path/to/my-research-project"
}'
```

Add a junior slot under `senior-01`:

```bash
research-agent-team-codex command add_junior --payload-json '{
  "root_path": "/absolute/path/to/my-research-project",
  "parent_slot_id": "senior-01"
}'
```

Retire a slot:

```bash
research-agent-team-codex command retire_junior --payload-json '{
  "root_path": "/absolute/path/to/my-research-project",
  "slot_id": "junior-01"
}'
```

Staffing changes may require approval depending on project policy and state. If a command returns a pending approval instead of a direct change, resolve it with `approve_checkpoint` or `reject_checkpoint`.

### 4. Interpret Natural-Language Requests

Before executing a user request written in natural language, ask the interpreter for a command plan:

```bash
research-agent-team-codex interpret \
  --root-path "/absolute/path/to/my-research-project" \
  --text "assign senior-01 a literature review task to review shared/raw"
```

The interpreter is read-only. It returns:

- `intent`
- `confidence`
- `command_name`
- `payload`
- `missing_fields`
- `ambiguous_references`
- `resolved_references`
- `needs_confirmation`
- `confirmation_reason`
- `warnings`
- `validation_errors`
- `ready_for_execution`

Default `conservative` confirmation mode requires confirmation for lifecycle changes, task assignment, experiments, approvals, and full rebuilds. Use `--confirmation-mode aggressive` only when the caller explicitly wants fewer confirmation gates for unambiguous plans.

### 5. Assign Work

Assign a task to a slot with `assign_task`. Clear `success_criteria` make conservative auto-launch possible for normal non-experiment tasks.

```bash
research-agent-team-codex command assign_task --payload-json '{
  "root_path": "/absolute/path/to/my-research-project",
  "requester_slot_id": "supervisor",
  "owner_slot_id": "senior-01",
  "title": "Review recent papers",
  "description": "Read shared/raw and produce a concise literature summary with next-step recommendations.",
  "success_criteria": [
    "Write a Markdown literature summary",
    "List open questions",
    "Recommend follow-up tasks"
  ],
  "input_artifact_ids": [],
  "input_path_roots": ["shared/raw"],
  "expected_output_types": ["markdown"],
  "budget_override": {}
}'
```

A successful assignment can return:

- `launch_request`: work was admitted and needs launch handling.
- queued state: the owner slot is busy and work will be admitted later.
- pending approval: policy requires approval before the effect is replayed.
- blocked state: the task cannot safely proceed.

### 6. Classify Launch Requests

Do not launch directly from raw command output. Use an attached MCP `launch_plan`, or classify the full command result with the CLI:

```bash
research-agent-team-codex plan-launches \
  --root-path "/absolute/path/to/my-research-project" \
  --source-command assign_task \
  --payload-file command-result.json
```

Launch decisions:

- `auto_launch`: Codex may render the prompt and launch a worker.
- `confirm_launch`: ask the user before launching.
- `error`: do not launch; report the task, slot, activation, and path details.
- `blocked`: command failed or launch handling was skipped.

The default `conservative` policy auto-launches only clear, non-experiment, non-approval activations that are still in `starting` state and have a low-risk budget. Experiments, approval replay, review follow-ups, unclear scope, stale state, and high-budget work require confirmation or error handling.

### 7. Render A Worker Prompt

After a launch decision is approved, render the sanitized launch request:

```bash
research-agent-team-codex render-launch-prompt \
  --root-path "/absolute/path/to/my-research-project" \
  --payload-file launch-request.json > worker-prompt.md
```

If you are using MCP, pass the approved `launch_request` object to `render_launch_prompt`. The worker should receive only the rendered prompt and the bundle context embedded in that prompt.

### 8. Complete Or Fail An Activation

Workers must call `mark-running` before doing work:

```bash
research-agent-team-codex activation mark-running \
  --root-path "/absolute/path/to/my-research-project" \
  --activation-id activation-id
```

Workers can checkpoint durable progress:

```bash
research-agent-team-codex activation checkpoint \
  --root-path "/absolute/path/to/my-research-project" \
  --activation-id activation-id \
  --payload-json '{
    "summary": "Reviewed the first batch of notes.",
    "resume_instructions": "Continue with shared/raw/paper-notes-2.md before drafting the final summary.",
    "output_artifacts": []
  }'
```

To complete a normal task, first make sure any referenced output artifact files already exist under an allowed project-relative path. Then report them:

```bash
research-agent-team-codex activation complete \
  --root-path "/absolute/path/to/my-research-project" \
  --activation-id activation-id \
  --payload-json '{
    "output_artifacts": [
      {
        "path": "agents/senior-01/results/literature-review.md",
        "type": "markdown",
        "visibility": "ancestor_visible"
      }
    ]
  }'
```

To fail an activation:

```bash
research-agent-team-codex activation fail \
  --root-path "/absolute/path/to/my-research-project" \
  --activation-id activation-id \
  --payload-json '{
    "failure_summary": "The required input files under shared/raw were missing."
  }'
```

Other supported activation callbacks are `heartbeat`, `interrupt`, and `cancel`. After every terminal callback, inspect the result for `next_launch_request` and run the same launch planning flow again.

### 9. Request Status

Use `request_status` as the routine operator snapshot:

```bash
research-agent-team-codex command request_status --payload-json '{
  "root_path": "/absolute/path/to/my-research-project"
}'
```

This writes `shared/reports/status-latest.md`, indexes it as an artifact, dispatches matching hooks, and returns counters for active work, queued work, approvals, warnings, and recent artifacts.

### 10. Sync Knowledge And Rebuild Graphs

Sync project-level knowledge:

```bash
research-agent-team-codex command sync_knowledge_base --payload-json '{
  "root_path": "/absolute/path/to/my-research-project",
  "scope_type": "project",
  "scope_id": null,
  "mode": "incremental"
}'
```

Sync a slot-local knowledge view:

```bash
research-agent-team-codex command sync_knowledge_base --payload-json '{
  "root_path": "/absolute/path/to/my-research-project",
  "scope_type": "slot",
  "scope_id": "senior-01",
  "mode": "incremental"
}'
```

Rebuild graph outputs:

```bash
research-agent-team-codex command rebuild_graph --payload-json '{
  "root_path": "/absolute/path/to/my-research-project",
  "mode": "incremental"
}'
```

Use `mode: "full"` when you intentionally want a full rebuild. Full rebuilds may require confirmation in conservative workflows.

### 11. Run And Review Experiments

`run_experiment` creates normal task state plus experiment coordination records. It may return a `launch_request`, but experiment activations require confirmation under the conservative launch policy.

```bash
research-agent-team-codex command run_experiment --payload-json '{
  "root_path": "/absolute/path/to/my-research-project",
  "requester_slot_id": "senior-01",
  "executor_slot_id": "junior-01",
  "title": "Evaluate baseline prompt",
  "objective": "Measure the baseline prompt on a fixture task.",
  "hypothesis": "The baseline produces a valid structured result.",
  "method": "Run the local-file experiment adapter once and publish outputs.",
  "success_criteria": ["Publish a reviewable evidence package"],
  "input_artifact_ids": [],
  "input_path_roots": ["shared/raw"],
  "expected_output_types": ["json", "markdown"],
  "run_parameters": {"prompt_variant": "baseline"},
  "compare_run_ids": [],
  "budget_override": {}
}'
```

After the experiment activation completes, review the experiment run:

```bash
research-agent-team-codex command review_experiment --payload-json '{
  "root_path": "/absolute/path/to/my-research-project",
  "experiment_run_id": "experiment-run-id",
  "reviewer_slot_id": "senior-01",
  "outcome": "accepted",
  "decision_summary": "Evidence is sufficient for the next milestone."
}'
```

For follow-up work, use `outcome: "needs_follow_up"` and include:

```json
{
  "follow_up_owner_slot_id": "junior-01",
  "follow_up_title": "Repeat baseline with larger fixture set",
  "follow_up_description": "Run the same method on the expanded fixture set and compare results.",
  "follow_up_success_criteria": ["Publish updated evidence and comparison notes"]
}
```

### 12. Generate Reports

Generate a final package:

```bash
research-agent-team-codex command generate_report --payload-json '{
  "root_path": "/absolute/path/to/my-research-project",
  "report_type": "final_package",
  "scope_type": "project",
  "scope_id": null
}'
```

Supported `report_type` values are:

- `status`
- `topology`
- `pending_approvals`
- `next_steps`
- `literature_review`
- `experiment_summary`
- `final_package`

Supported `scope_type` values are `project`, `slot`, `task`, and `experiment_run`.

## Command Cheat Sheet

| Area | Commands |
| --- | --- |
| Project lifecycle | `create_project`, `open_project`, `switch_operating_mode`, `pause_project`, `resume_project` |
| Team topology | `show_team_topology`, `add_senior`, `add_junior`, `retire_senior`, `retire_junior` |
| Work | `assign_task` |
| Approvals | `approve_checkpoint`, `reject_checkpoint` |
| Reports | `request_status`, `generate_report` |
| Knowledge and graph | `sync_knowledge_base`, `rebuild_graph` |
| Experiments | `run_experiment`, `review_experiment` |
| Activation callbacks | `mark-running`, `heartbeat`, `checkpoint`, `complete`, `fail`, `interrupt`, `cancel` |
| Launch handling | `plan-launches`, `render-launch-prompt` |
| Natural language | `interpret` CLI mode or MCP `interpret_request` |

All public commands return one JSON envelope:

```json
{
  "ok": true,
  "result": {}
}
```

or:

```json
{
  "ok": false,
  "error": {
    "code": "stable_error_code",
    "message": "Operator-facing explanation"
  }
}
```

## Python Automation

For local automation, use the current Codex bridge. It returns the same JSON envelopes as the CLI.

```python
from research_agent_team.platform.codex import bridge

project_root = "/absolute/path/to/my-research-project"

created = bridge.run_command(
    "create_project",
    {
        "name": "Demo Research",
        "root_path": project_root,
        "initial_charter_text": "# Demo Research\n\nInitial scope.\n",
    },
)
if not created["ok"]:
    raise RuntimeError(created["error"])

opened = bridge.run_command("open_project", {"root_path": project_root})
if not opened["ok"]:
    raise RuntimeError(opened["error"])

assigned = bridge.run_command(
    "assign_task",
    {
        "root_path": project_root,
        "requester_slot_id": "supervisor",
        "owner_slot_id": "senior-01",
        "title": "Review recent papers",
        "description": "Summarize the newest raw notes and propose next steps.",
        "success_criteria": ["Produce a concise literature summary"],
        "input_path_roots": ["shared/raw"],
        "expected_output_types": ["markdown"],
    },
)
if not assigned["ok"]:
    raise RuntimeError(assigned["error"])

with_launch_plan = bridge.with_launch_plan(
    assigned,
    project_root,
    source_command="assign_task",
    policy="conservative",
)
```

The older plugin adapter command imports are not the current public entry point. Prefer MCP workflow tools, `research-agent-team-codex`, or `research_agent_team.platform.codex.bridge`.

## Operator Guidance

Use command results and generated reports first. Inspect canonical JSON only when diagnosing a specific issue.

Recommended return-to-project routine:

1. Run `open_project`.
2. Run `request_status`.
3. Read `shared/reports/status-latest.md`.
4. Inspect warnings, pending approvals, queued work, and recent artifacts.
5. Run `resume_project` only when queued work should continue.

Warnings are non-fatal when core command logic succeeds. Common warning sources include repaired support surfaces, degraded optional adapters, hook delivery failures, missing optional report sections, or stale activation recovery.

If a command reports missing canonical business state, such as a missing task, slot, activation, approval, or experiment record, restore that canonical file from backup before rerunning commands. Preflight may repair support directories and derived views, but it must not fabricate missing canonical state.

For activation problems:

- Run `open_project` to trigger stale activation recovery.
- Run `request_status` to inspect blocked, queued, and active work.
- Inspect `state/activations/<activation-id>.json`.
- Inspect `state/tasks/<task-id>.json` and `state/slots/<slot-id>.json`.
- Use `resume_project` when recovered queued work should continue.

For adapter problems:

- Check `state/adapters/health.json`.
- Graph adapter failures should degrade graph outputs without corrupting task state.
- Experiment adapter failures should stop before fabricating successful evidence.

For hook problems:

- Check `state/hooks/config.json`.
- Check `logs/hooks/YYYY-MM-DD.jsonl`.
- Disable or fix noisy local hook subscribers. Hooks are best-effort and must not be the only route for canonical state transitions.

## Development Checks

Run these from the monorepo root:

```bash
python plugins/research-agent-team/scripts/rat_plugin_cli.py --help
python plugins/research-agent-team/scripts/rat_plugin_cli.py command --help
python plugins/research-agent-team/scripts/validate_manifest.py
python plugins/research-agent-team/scripts/validate_schemas.py
uv run pytest
bash scripts/validate-release-boundary.sh
bash scripts/smoke-test-plugin.sh
bash scripts/package-plugin.sh
```

Focused README-related checks:

```bash
uv run pytest tests/plugin/test_rat_plugin_cli.py tests/plugin/test_stage0_plugin_skeleton.py tests/unit/test_stage7_docs_and_release.py
```

The release package boundary is `plugins/research-agent-team/`. Root `docs/`, root `tests/`, `.github/`, `.agents/`, root changelog, and root release scripts are not part of the installable plugin subtree.

## More Documentation

- [Command contract](./docs/contracts/commands.md)
- [Project state contract](./docs/contracts/project-state.md)
- [Adapter contract](./docs/contracts/adapters.md)
- [Hook contract](./docs/contracts/hooks.md)
- [Operator guide](./docs/operator-guide.md)
- [Activation failures runbook](./docs/runbooks/activation-failures.md)
- [Release checklist](./docs/runbooks/release-checklist.md)
- [Plugin README](./plugins/research-agent-team/README.md)

## Current Availability

ResearchAgentTeam currently runs locally as an installable Codex plugin plus Python command bridge. It is focused on a single research project on a local machine, with durable files and generated reports as the main working surface.
