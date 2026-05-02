# ResearchAgentTeam

[English](./README.md)

ResearchAgentTeam 是一个 Codex 优先、本地优先的研究编排插件。它帮助研究者把长期计算型研究项目持续运行下去，而不是每次都从新的聊天上下文重新开始。团队结构、任务流转、activation 状态、报告、实验依据和可复用知识都会写入本地文件系统。

主 Codex 会话扮演 `supervisor`。持久化的 `senior_phd` 和 `junior_phd` 槽位保存在项目状态里，并通过显式命令被激活。系统不把聊天记录当成唯一记忆，而是让项目状态可以检查、恢复和复用。

## 它提供什么

- 持久化团队记忆：稳定的 supervisor、senior、junior 槽位和可持久化状态。
- 文件系统优先输出：报告、笔记、图谱摘要、实验依据和 wiki 页面都会保留在项目根目录。
- 显式任务流：任务通过命令结果完成分配、准入、启动、checkpoint、完成、失败或评审。
- 安全的 Codex 启动流程：launch request 会先被分类，再渲染 worker prompt。
- 知识沉淀：共享材料可以编译成可复用的 wiki 风格输出。
- 实验工作流：senior 定义、junior 执行的实验会产出 evidence package 和 review record。
- 操作者可见性：状态报告和最终报告包会汇总进展、审批、警告和下一步工作。

## 仓库结构

本仓库按源码 monorepo 维护。真正可安装的 Codex 插件位于 `plugins/research-agent-team/`，根目录的 docs、tests 和发布脚本是开发资产。

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

创建研究项目后，runtime 会生成一个独立的项目根目录，例如：

```text
MyResearchProject/
├── project.yaml
├── state/
├── agents/
├── shared/
├── experiments/
└── logs/
```

重要生成路径：

- `project.yaml`：便于阅读的项目 manifest。
- `state/project.json`：规范项目记录。
- `state/topology/current.json`：当前团队拓扑。
- `state/slots/*.json`：supervisor、senior、junior 槽位记录。
- `state/tasks/*.json`：任务记录。
- `state/activations/*.json`：worker activation 记录。
- `state/approvals/*.json`：待处理和已处理审批。
- `state/artifacts/index.jsonl`：append-only artifact 索引。
- `shared/reports/status-latest.md`：最新状态报告。
- `shared/reports/final-package-latest.md`：最新最终报告包。
- `shared/wiki/`：编译后的知识页面。
- `shared/graph/`：图谱 JSON 与 Markdown 摘要。
- `experiments/runs/`：实验运行依据和 publish manifest。
- `logs/hooks/YYYY-MM-DD.jsonl`：本地 hook 投递诊断。

## 安装与验证

ResearchAgentTeam 需要 Python 3.12 或更新版本。

在 monorepo 根目录安装插件包：

```bash
python -m pip install -e plugins/research-agent-team
```

验证已安装的命令桥：

```bash
research-agent-team-codex --help
research-agent-team-codex command --help
```

如果直接使用源码 checkout，也可以不依赖 console script，而是运行插件内 CLI wrapper：

```bash
python plugins/research-agent-team/scripts/rat_plugin_cli.py --help
python plugins/research-agent-team/scripts/rat_plugin_cli.py command --help
```

可安装插件的 metadata 位于：

```text
plugins/research-agent-team/.codex-plugin/plugin.json
```

指导 Codex 如何监督项目的 skill 位于：

```text
plugins/research-agent-team/skills/research-agent-team/SKILL.md
```

插件也通过下面的文件注册本地 STDIO MCP server：

```text
plugins/research-agent-team/mcp/.mcp.json
```

在源码 checkout 中，可以从插件根目录启动 MCP server：

```bash
cd plugins/research-agent-team
uv run --project . python ./scripts/rat_plugin_mcp.py
```

## 推荐的 Codex 工作流

当 MCP tools 可用时，Codex 应优先使用 MCP tools，而不是直接 shell 到 CLI：

- `interpret_request`：把自然语言映射为命令计划，不修改状态。
- `run_command`：运行公开项目命令，并在可获得 `root_path` 时附带保守的 `launch_plan`。
- `activation_callback`：更新 activation 状态，并为后续工作附带 launch planning。
- `plan_launches`：分类上一个 command 或 callback result 中的 launch request。
- `render_launch_prompt`：把已批准的 launch request 渲染成 worker prompt。

MCP tools 不会直接启动 worker。它们返回足够的结构，让 Codex 决定是否自动启动、请求确认或报告错误。

当 MCP tools 不可用，或你在终端里操作时，使用 CLI bridge：

```bash
research-agent-team-codex command <command_name> --payload-json '{...}'
research-agent-team-codex command <command_name> --payload-file payload.json
printf '{"root_path":"/absolute/project"}' | research-agent-team-codex command open_project
```

## 完整使用教程

下面的示例使用绝对项目路径。请替换成你自己的路径：

```bash
PROJECT_ROOT="/absolute/path/to/my-research-project"
```

### 1. 创建项目

`create_project` 会创建项目根目录、state layout、supervisor 槽位、默认 `senior-01` 槽位、policy、budget、adapter health、hook config 和初始 charter artifact。目标根目录必须不存在或为空。

```bash
research-agent-team-codex command create_project --payload-json '{
  "name": "Demo Research",
  "root_path": "/absolute/path/to/my-research-project",
  "initial_charter_text": "# Demo Research\n\nInitial scope and research questions.\n"
}'
```

预期命令 envelope：

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

如果使用 MCP，则调用 `run_command` 并传入：

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

### 2. 打开或恢复项目

每次回到已有项目时，先运行 `open_project`。它会在项目锁内运行 preflight，执行受支持的 migration，修复 support surface，规范化 adapter health，恢复 stale activation，重建派生视图，并返回最新 operator snapshot。

```bash
research-agent-team-codex command open_project --payload-json '{
  "root_path": "/absolute/path/to/my-research-project"
}'
```

优先阅读这个结果，不要一开始就翻 raw JSON。warnings 通常是非致命的；致命完整性错误会返回 `ok: false` 和稳定的 `error.code`。

### 3. 查看或调整团队

查看当前拓扑：

```bash
research-agent-team-codex command show_team_topology --payload-json '{
  "root_path": "/absolute/path/to/my-research-project"
}'
```

添加 senior 槽位：

```bash
research-agent-team-codex command add_senior --payload-json '{
  "root_path": "/absolute/path/to/my-research-project"
}'
```

在 `senior-01` 下添加 junior 槽位：

```bash
research-agent-team-codex command add_junior --payload-json '{
  "root_path": "/absolute/path/to/my-research-project",
  "parent_slot_id": "senior-01"
}'
```

退休某个槽位：

```bash
research-agent-team-codex command retire_junior --payload-json '{
  "root_path": "/absolute/path/to/my-research-project",
  "slot_id": "junior-01"
}'
```

根据项目 policy 和当前状态，staffing 变更可能需要审批。如果命令返回 pending approval 而不是直接变更，请使用 `approve_checkpoint` 或 `reject_checkpoint` 处理。

### 4. 解释自然语言请求

执行自然语言请求前，先让 interpreter 生成命令计划：

```bash
research-agent-team-codex interpret \
  --root-path "/absolute/path/to/my-research-project" \
  --text "assign senior-01 a literature review task to review shared/raw"
```

interpreter 是只读的。它会返回：

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

默认 `conservative` confirmation mode 会要求对生命周期变更、任务分配、实验、审批和 full rebuild 进行确认。只有当调用方明确希望减少非歧义计划的确认门槛时，才使用 `--confirmation-mode aggressive`。

### 5. 分配工作

使用 `assign_task` 把任务分配给某个槽位。清晰的 `success_criteria` 能让普通非实验任务更容易通过保守策略自动启动。

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

成功分配后可能返回：

- `launch_request`：工作已准入，需要 launch handling。
- queued state：owner 槽位正忙，工作会稍后准入。
- pending approval：policy 要求审批后再 replay effect。
- blocked state：任务无法安全继续。

### 6. 分类 Launch Request

不要直接从原始 command output 启动 worker。请使用 MCP 附带的 `launch_plan`，或通过 CLI 分类完整 command result：

```bash
research-agent-team-codex plan-launches \
  --root-path "/absolute/path/to/my-research-project" \
  --source-command assign_task \
  --payload-file command-result.json
```

launch decision 含义：

- `auto_launch`：Codex 可以渲染 prompt 并启动 worker。
- `confirm_launch`：启动前询问用户。
- `error`：不要启动；报告 task、slot、activation 和路径细节。
- `blocked`：命令失败或 launch handling 被跳过。

默认 `conservative` 策略只会自动启动清晰、非实验、非审批、仍处于 `starting` 状态且 budget 风险较低的 activation。实验、approval replay、review follow-up、不清晰范围、stale state 和高 budget 工作都需要确认或错误处理。

### 7. 渲染 Worker Prompt

launch decision 被批准后，渲染经过净化的 launch request：

```bash
research-agent-team-codex render-launch-prompt \
  --root-path "/absolute/path/to/my-research-project" \
  --payload-file launch-request.json > worker-prompt.md
```

如果使用 MCP，把已批准的 `launch_request` object 传给 `render_launch_prompt`。worker 应只收到渲染后的 prompt，以及 prompt 中嵌入的 bundle context。

### 8. 完成或失败 Activation

worker 做任何实际工作前，必须先调用 `mark-running`：

```bash
research-agent-team-codex activation mark-running \
  --root-path "/absolute/path/to/my-research-project" \
  --activation-id activation-id
```

worker 可以持久化 checkpoint：

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

完成普通任务前，先确认所有被引用的 output artifact 文件已经存在，并且路径是允许的 project-relative path。然后回报：

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

失败 activation：

```bash
research-agent-team-codex activation fail \
  --root-path "/absolute/path/to/my-research-project" \
  --activation-id activation-id \
  --payload-json '{
    "failure_summary": "The required input files under shared/raw were missing."
  }'
```

其他支持的 activation callbacks 包括 `heartbeat`、`interrupt` 和 `cancel`。每次 terminal callback 后，都要检查结果里是否有 `next_launch_request`，并重复同样的 launch planning 流程。

### 9. 请求状态

把 `request_status` 作为日常 operator snapshot：

```bash
research-agent-team-codex command request_status --payload-json '{
  "root_path": "/absolute/path/to/my-research-project"
}'
```

它会写入 `shared/reports/status-latest.md`，把该报告索引为 artifact，投递匹配 hooks，并返回 active work、queued work、approvals、warnings 和 recent artifacts 计数。

### 10. 同步知识并重建图谱

同步项目级知识：

```bash
research-agent-team-codex command sync_knowledge_base --payload-json '{
  "root_path": "/absolute/path/to/my-research-project",
  "scope_type": "project",
  "scope_id": null,
  "mode": "incremental"
}'
```

同步槽位本地知识视图：

```bash
research-agent-team-codex command sync_knowledge_base --payload-json '{
  "root_path": "/absolute/path/to/my-research-project",
  "scope_type": "slot",
  "scope_id": "senior-01",
  "mode": "incremental"
}'
```

重建图谱输出：

```bash
research-agent-team-codex command rebuild_graph --payload-json '{
  "root_path": "/absolute/path/to/my-research-project",
  "mode": "incremental"
}'
```

当你明确需要完整重建时，使用 `mode: "full"`。在保守工作流里，full rebuild 可能需要确认。

### 11. 运行并评审实验

`run_experiment` 会创建普通 task state 和实验协调记录。它可能返回 `launch_request`，但在保守 launch policy 下，实验 activation 需要确认。

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

实验 activation 完成后，评审 experiment run：

```bash
research-agent-team-codex command review_experiment --payload-json '{
  "root_path": "/absolute/path/to/my-research-project",
  "experiment_run_id": "experiment-run-id",
  "reviewer_slot_id": "senior-01",
  "outcome": "accepted",
  "decision_summary": "Evidence is sufficient for the next milestone."
}'
```

如果需要 follow-up work，使用 `outcome: "needs_follow_up"`，并包含：

```json
{
  "follow_up_owner_slot_id": "junior-01",
  "follow_up_title": "Repeat baseline with larger fixture set",
  "follow_up_description": "Run the same method on the expanded fixture set and compare results.",
  "follow_up_success_criteria": ["Publish updated evidence and comparison notes"]
}
```

### 12. 生成报告

生成最终报告包：

```bash
research-agent-team-codex command generate_report --payload-json '{
  "root_path": "/absolute/path/to/my-research-project",
  "report_type": "final_package",
  "scope_type": "project",
  "scope_id": null
}'
```

支持的 `report_type`：

- `status`
- `topology`
- `pending_approvals`
- `next_steps`
- `literature_review`
- `experiment_summary`
- `final_package`

支持的 `scope_type` 为 `project`、`slot`、`task` 和 `experiment_run`。

## 命令速查

| 领域 | 命令 |
| --- | --- |
| 项目生命周期 | `create_project`, `open_project`, `switch_operating_mode`, `pause_project`, `resume_project` |
| 团队拓扑 | `show_team_topology`, `add_senior`, `add_junior`, `retire_senior`, `retire_junior` |
| 工作流 | `assign_task` |
| 审批 | `approve_checkpoint`, `reject_checkpoint` |
| 报告 | `request_status`, `generate_report` |
| 知识与图谱 | `sync_knowledge_base`, `rebuild_graph` |
| 实验 | `run_experiment`, `review_experiment` |
| Activation callbacks | `mark-running`, `heartbeat`, `checkpoint`, `complete`, `fail`, `interrupt`, `cancel` |
| Launch handling | `plan-launches`, `render-launch-prompt` |
| 自然语言 | CLI `interpret` mode 或 MCP `interpret_request` |

所有公开命令都会返回一个 JSON envelope：

```json
{
  "ok": true,
  "result": {}
}
```

或者：

```json
{
  "ok": false,
  "error": {
    "code": "stable_error_code",
    "message": "Operator-facing explanation"
  }
}
```

## Python 自动化

本地自动化应使用当前 Codex bridge。它返回的 JSON envelope 与 CLI 一致。

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

旧的 plugin adapter command imports 不是当前公开入口。请优先使用 MCP workflow tools、`research-agent-team-codex`，或 `research_agent_team.platform.codex.bridge`。

## 操作者指南

优先使用命令结果和生成报告。只有诊断具体问题时，才直接检查 canonical JSON。

推荐的返回项目流程：

1. 运行 `open_project`。
2. 运行 `request_status`。
3. 阅读 `shared/reports/status-latest.md`。
4. 检查 warnings、pending approvals、queued work 和 recent artifacts。
5. 只有当 queued work 应继续推进时，才运行 `resume_project`。

当核心命令逻辑成功时，warnings 是非致命的。常见 warning 来源包括 support surface 修复、可选 adapter degraded、hook delivery failure、缺少可选报告部分，或 stale activation recovery。

如果命令报告缺少 canonical business state，例如缺少 task、slot、activation、approval 或 experiment 记录，请先从备份恢复该 canonical 文件，再重新运行命令。Preflight 可以修复 support directories 和 derived views，但不能伪造缺失的 canonical state。

Activation 问题处理：

- 运行 `open_project` 触发 stale activation recovery。
- 运行 `request_status` 查看 blocked、queued 和 active work。
- 检查 `state/activations/<activation-id>.json`。
- 检查 `state/tasks/<task-id>.json` 和 `state/slots/<slot-id>.json`。
- 当恢复出的 queued work 需要继续时，使用 `resume_project`。

Adapter 问题处理：

- 检查 `state/adapters/health.json`。
- Graph adapter 失败时，应 degrade graph outputs，而不是破坏 task state。
- Experiment adapter 失败时，应在伪造成功 evidence 前停止。

Hook 问题处理：

- 检查 `state/hooks/config.json`。
- 检查 `logs/hooks/YYYY-MM-DD.jsonl`。
- 禁用或修复噪声过大的本地 hook subscribers。Hooks 是 best-effort，不能作为 canonical state transition 的唯一路径。

## 开发检查

在 monorepo 根目录运行：

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

与 README 相关的 focused checks：

```bash
uv run pytest tests/plugin/test_rat_plugin_cli.py tests/plugin/test_stage0_plugin_skeleton.py tests/unit/test_stage7_docs_and_release.py
```

发布包边界是 `plugins/research-agent-team/`。根目录 `docs/`、根目录 `tests/`、`.github/`、`.agents/`、根 changelog 和根发布脚本都不属于可安装插件 subtree。

## 更多文档

- [Command contract](./docs/contracts/commands.md)
- [Project state contract](./docs/contracts/project-state.md)
- [Adapter contract](./docs/contracts/adapters.md)
- [Hook contract](./docs/contracts/hooks.md)
- [Operator guide](./docs/operator-guide.md)
- [Activation failures runbook](./docs/runbooks/activation-failures.md)
- [Release checklist](./docs/runbooks/release-checklist.md)
- [Plugin README](./plugins/research-agent-team/README.md)

## 当前可用性

ResearchAgentTeam 当前在本地运行，形态是可安装 Codex 插件加 Python 命令桥。它面向单个本地研究项目，核心工作界面是持续保存的文件和生成报告。
