# ResearchAgentTeam

[中文说明](./README.zh-CN.md)

ResearchAgentTeam helps researchers run long-lived computational projects with a persistent research team structure instead of starting from scratch in every chat. It keeps project memory, task flow, reports, and knowledge outputs on disk so work can build over time.

## Project Introduction

Research projects rarely fit into one disposable session. They usually need:

- persistent roles and shared context
- a clear way to assign and review work
- reusable knowledge instead of repeated summarization
- durable reports and reusable project knowledge

ResearchAgentTeam is built for that style of work. It organizes a project around a stable team with:

- `supervisor`: the lead role that sets direction and priorities
- `senior_phd`: senior research slots that break down work and review results
- `junior_phd`: junior research slots that carry out experiments and coding tasks

Instead of relying on chat history as the only memory, the system writes project state and outputs to the filesystem so they remain inspectable and reusable.

## Advantages

ResearchAgentTeam is designed to be useful in day-to-day research work:

- Persistent team memory: the project keeps stable roles, histories, and working areas over time.
- Filesystem-first outputs: reports, notes, experiment artifacts, and shared materials stay on disk where they can be reviewed directly.
- Clear task flow: work is assigned explicitly, reviewed explicitly, and tracked as part of the project.
- Knowledge accumulation: raw materials can be turned into reusable wiki-style knowledge outputs.
- Experiment support: experiment requests, outputs, review decisions, and follow-up work stay connected to normal task flow.
- Better visibility: status reports and final reports make it easier to see what has happened and what should happen next.

## What You Can Do

With the current product, you can:

- create and reopen a research project
- add senior and junior roles to the team
- assign research or coding tasks
- sync knowledge into shared outputs
- rebuild graph-style project views
- generate status reports and final report packages

When a project is created, it produces a local project workspace such as:

```text
ResearchProject/
├── project.yaml
├── state/
├── agents/
├── shared/
└── experiments/
```

Common user-facing outputs include:

- `shared/reports/status-latest.md`: latest project status report
- `shared/reports/final-package-latest.md`: latest final report package
- `shared/wiki/`: compiled knowledge pages
- `shared/graph/`: graph outputs and summaries
- `experiments/runs/`: saved experiment outputs, summaries, comparisons, and publish manifests

## Getting Started

ResearchAgentTeam is maintained as a source monorepo. The installable Codex plugin lives under `plugins/research-agent-team/`, while repo-level docs, tests, CI, and release tooling stay at the monorepo root.

### Install as a Codex plugin

Clone this repository as the monorepo root. The installable plugin manifest is included at:

```text
plugins/research-agent-team/.codex-plugin/plugin.json
```

The plugin skill that tells Codex how to supervise projects is included at:

```text
plugins/research-agent-team/skills/research-agent-team/SKILL.md
```

Install the Python package from the plugin root so the command bridge is available:

```bash
python -m pip install -e plugins/research-agent-team
```

After installation, verify the command bridge:

```bash
research-agent-team-codex --help
```

From a source checkout, you can also run the bridge without relying on the console script:

```bash
python plugins/research-agent-team/scripts/rat_plugin_cli.py --help
```

The installable plugin also registers a local STDIO MCP server through
`plugins/research-agent-team/mcp/.mcp.json`. Codex can call workflow tools for
natural-language interpretation, command execution with launch planning,
activation callbacks with follow-up planning, standalone launch classification,
and launch prompt rendering. The MCP server can be started from a source
checkout with:

```bash
cd plugins/research-agent-team
uv run --project . python ./scripts/rat_plugin_mcp.py
```

### Interpret natural-language requests

Before executing a natural-language request, ask the interpreter for a command
plan:

```bash
research-agent-team-codex interpret \
  --root-path "/absolute/path/to/my-research-project" \
  --text "assign senior-01 a literature review task to review shared/raw"
```

The interpreter returns structured JSON with the inferred command payload,
missing or ambiguous fields, validation warnings, and whether confirmation is
required. It does not execute the command.

### Use the plugin command bridge

The main Codex session should use the command bridge to create, open, inspect, and operate projects:

```bash
research-agent-team-codex command open_project --payload-json '{"root_path":"/absolute/path/to/my-research-project"}'
```

Payloads can also come from a file or stdin:

```bash
research-agent-team-codex command open_project --payload-file payload.json
printf '{"root_path":"/absolute/path/to/my-research-project"}' | research-agent-team-codex command open_project
```

Commands that admit work, such as `assign_task`, may return a non-null `launch_request`. Codex should classify launch requests before rendering worker prompts:

```bash
research-agent-team-codex plan-launches --root-path "/absolute/path/to/my-research-project" --source-command assign_task --payload-file command-result.json
research-agent-team-codex render-launch-prompt --root-path "/absolute/path/to/my-research-project" --payload-file launch-request.json
```

`plan-launches` is read-only. It classifies launch requests as automatic, confirmation-required, or invalid under a conservative policy. The worker prompt includes the task bundle, briefing, runtime metadata, and activation callback commands. Workers must mark the activation running, then complete or fail it through the same bridge:

```bash
research-agent-team-codex activation mark-running --root-path "/absolute/path/to/my-research-project" --activation-id activation-id
research-agent-team-codex activation complete --root-path "/absolute/path/to/my-research-project" --activation-id activation-id --payload-file completion.json
research-agent-team-codex activation fail --root-path "/absolute/path/to/my-research-project" --activation-id activation-id --payload-file failure.json
```

### Use as a Python library

The existing Python command adapter remains available for local automation and tests.

When calling it from a monorepo checkout, install the plugin package first or set `PYTHONPATH=plugins/research-agent-team/src`.

#### Create and open a project

```python
from research_agent_team.plugin_adapter.commands import (
    create_project_command,
    open_project_command,
)

project_root = "/absolute/path/to/my-research-project"

create_project_command(
    {
        "name": "Demo Research",
        "root_path": project_root,
        "initial_charter_text": "# Demo Research Charter\n\nInitial scope.\n",
    }
)

open_project_command({"root_path": project_root})
```

#### Add a junior role and assign work

```python
from research_agent_team.plugin_adapter.commands import (
    add_junior_command,
    assign_task_command,
)

add_junior_command(
    {
        "root_path": project_root,
        "parent_slot_id": "senior-01",
    }
)

assign_task_command(
    {
        "root_path": project_root,
        "requester_slot_id": "supervisor",
        "owner_slot_id": "senior-01",
        "title": "Review recent papers",
        "description": "Summarize the newest raw notes and propose next steps.",
        "success_criteria": [
            "Produce a concise literature summary",
            "List follow-up actions",
        ],
        "input_artifact_ids": [],
        "input_path_roots": ["shared/raw"],
        "expected_output_types": ["markdown"],
        "budget_override": {},
        "review_requirement": "none",
    }
)
```

#### Build shared knowledge outputs

```python
from research_agent_team.plugin_adapter.commands import (
    rebuild_graph_command,
    sync_knowledge_base_command,
)

sync_knowledge_base_command(
    {
        "root_path": project_root,
        "scope_type": "project",
        "scope_id": None,
        "mode": "incremental",
    }
)

rebuild_graph_command(
    {
        "root_path": project_root,
        "mode": "incremental",
    }
)
```

#### Run and review an experiment

```bash
python plugins/research-agent-team/scripts/rat_plugin_cli.py command run_experiment --payload-json '{
  "root_path": "/absolute/project",
  "requester_slot_id": "senior-01",
  "executor_slot_id": "junior-01",
  "title": "Evaluate baseline",
  "objective": "Measure baseline behavior on the fixture task.",
  "hypothesis": "The baseline produces repeatable evidence.",
  "method": "Run the local-file experiment adapter once and publish outputs.",
  "success_criteria": ["Publish a reviewable evidence package"],
  "input_artifact_ids": [],
  "input_path_roots": ["shared/raw"],
  "expected_output_types": ["json", "markdown"],
  "run_parameters": {"variant": "baseline"}
}'

python plugins/research-agent-team/scripts/rat_plugin_cli.py command review_experiment --payload-json '{
  "root_path": "/absolute/project",
  "experiment_run_id": "experiment-run-id",
  "reviewer_slot_id": "senior-01",
  "outcome": "accepted",
  "decision_summary": "Evidence is sufficient for the next milestone."
}'
```

#### Generate status and final reports

```python
from research_agent_team.plugin_adapter.commands import (
    generate_report_command,
    request_status_command,
)

request_status_command({"root_path": project_root})

generate_report_command(
    {
        "root_path": project_root,
        "report_type": "final_package",
        "scope_type": "project",
        "scope_id": None,
    }
)
```

## Common Tasks

The current commands cover these user workflows:

- create, open, pause, resume, and switch project mode
- add and retire team roles
- assign work and review progress
- sync knowledge and rebuild graph outputs
- run experiments, review evidence, and create follow-up tasks
- generate status reports and final packages

## Current Availability

ResearchAgentTeam currently runs locally as an installable Codex plugin plus Python command bridge. The product is focused on a single research project on a local machine, with durable files and reports as the main working surface.
