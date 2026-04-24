# ResearchAgentTeam

[English](./README.md)

ResearchAgentTeam 用来帮助研究者运行长期的计算型研究项目。它不是让每一次工作都从新的聊天开始，而是通过持久化的研究团队结构，把项目记忆、任务流转、报告、知识输出和实验结果持续保存在本地。

## 项目简介

研究项目通常不适合只依赖一次性的代理会话。它们更需要：

- 持久化的角色和共享上下文
- 清晰的任务分配与评审方式
- 可重复利用的知识沉淀
- 持久保存的实验结果和报告

ResearchAgentTeam 就是为这类研究方式设计的。它把一个项目组织成一个稳定的团队，包括：

- `supervisor`：负责方向和优先级的主导角色
- `senior_phd`：负责拆解工作和审阅结果的高级研究槽位
- `junior_phd`：负责执行实验和编码任务的初级研究槽位

系统不会只依赖聊天记录来保存记忆，而是把项目状态和主要输出写入文件系统，让它们能够被持续查看、复用和积累。

## 优势

ResearchAgentTeam 在实际研究工作中的价值主要体现在：

- 持久化团队记忆：项目中的角色、历史和工作区域会随着时间保留下来。
- 文件系统优先：报告、笔记、实验工件和共享资料都会直接保存在磁盘上，便于查看和追踪。
- 清晰的任务流：工作以明确的方式被分配、审阅和记录，而不是散落在临时对话中。
- 知识可积累：原始材料可以整理成可复用的 wiki 风格知识输出。
- 实验支持：实验请求、输出和后续工作会自然关联在一起，不会散落到不同会话里。
- 更好的可见性：状态报告和最终报告包能帮助你更快了解项目进展以及下一步该做什么。

## 你可以做什么

使用当前产品，你可以：

- 创建并重新打开研究项目
- 为团队添加 senior 和 junior 角色
- 分配研究任务或编码任务
- 同步知识并生成共享输出
- 重建图谱类项目视图
- 运行实验并评审结果
- 生成状态报告和最终报告包

创建项目后，系统会生成类似下面的本地工作空间：

```text
ResearchProject/
├── project.yaml
├── state/
├── agents/
├── shared/
└── experiments/
```

常见的用户可见输出包括：

- `shared/reports/status-latest.md`：最新项目状态报告
- `shared/reports/final-package-latest.md`：最新最终报告包
- `shared/wiki/`：整理后的知识页面
- `shared/graph/`：图谱输出与摘要
- `experiments/runs/`：保存下来的实验结果

## 快速开始

ResearchAgentTeam 现在按源码 monorepo 维护。真正可安装的 Codex 插件位于 `plugins/research-agent-team/`，而仓库根目录保留文档、测试、CI 和发布脚本等工程资产。

### 作为 Codex 插件安装

将本仓库作为 monorepo 根目录克隆。可安装插件的 manifest 位于：

```text
plugins/research-agent-team/.codex-plugin/plugin.json
```

用于告诉 Codex 如何监督项目的插件 skill 位于：

```text
plugins/research-agent-team/skills/research-agent-team/SKILL.md
```

从插件根目录安装 Python 包，使命令桥可用：

```bash
python -m pip install -e plugins/research-agent-team
```

安装后可以先验证命令桥：

```bash
research-agent-team-codex --help
```

如果直接在源码仓库中使用，也可以运行：

```bash
python plugins/research-agent-team/scripts/rat_plugin_cli.py --help
```

### 使用插件命令桥

Codex 主会话应通过命令桥创建、打开、查看和操作项目：

```bash
research-agent-team-codex command open_project --payload-json '{"root_path":"/absolute/path/to/my-research-project"}'
```

payload 也可以来自文件或标准输入：

```bash
research-agent-team-codex command open_project --payload-file payload.json
printf '{"root_path":"/absolute/path/to/my-research-project"}' | research-agent-team-codex command open_project
```

`assign_task`、`run_experiment` 等命令在准入工作时，可能返回非空的 `launch_request`。Codex 应把这个 launch request 渲染成 worker prompt，并启动 subagent：

```bash
research-agent-team-codex render-launch-prompt --root-path "/absolute/path/to/my-research-project" --payload-file launch-request.json
```

worker prompt 会包含任务 bundle、briefing、runtime metadata，以及 activation 回调命令。worker 必须先把 activation 标记为 running，然后通过同一个命令桥完成或失败该 activation：

```bash
research-agent-team-codex activation mark-running --root-path "/absolute/path/to/my-research-project" --activation-id activation-id
research-agent-team-codex activation complete --root-path "/absolute/path/to/my-research-project" --activation-id activation-id --payload-file completion.json
research-agent-team-codex activation fail --root-path "/absolute/path/to/my-research-project" --activation-id activation-id --payload-file failure.json
```

### 作为 Python 库使用

原有 Python command adapter 仍可用于本地自动化和测试。

在 monorepo 中直接调用时，应先安装插件包，或设置 `PYTHONPATH=plugins/research-agent-team/src`。

#### 创建并打开项目

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

#### 添加 junior 角色并分配任务

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

#### 生成共享知识输出

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

#### 运行并评审实验

```python
from research_agent_team.plugin_adapter.commands import (
    review_experiment_command,
    run_experiment_command,
)

run_result = run_experiment_command(
    {
        "root_path": project_root,
        "requester_slot_id": "senior-01",
        "executor_slot_id": "junior-01",
        "title": "Evaluate baseline prompt",
        "objective": "Measure the baseline prompt on a fixture task.",
        "hypothesis": "The baseline produces a valid structured result.",
        "method": "Run the experiment once and publish outputs.",
        "success_criteria": ["Produce a published experiment result"],
        "input_artifact_ids": [],
        "input_path_roots": ["shared/reports"],
        "expected_output_types": ["json", "markdown"],
        "run_parameters": {"prompt_variant": "baseline"},
        "compare_run_ids": [],
        "budget_override": {},
    }
)

review_experiment_command(
    {
        "root_path": project_root,
        "experiment_run_id": run_result["experiment_run"]["experiment_run_id"],
        "reviewer_slot_id": "senior-01",
        "outcome": "accepted",
        "decision_summary": "The baseline is good enough for the next iteration.",
    }
)
```

#### 生成状态与最终报告

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

## 常见任务

当前命令已经覆盖这些常见工作流：

- 创建、打开、暂停、恢复项目，以及切换项目模式
- 添加和退休团队角色
- 分配工作并查看进展
- 同步知识并重建图谱输出
- 运行实验并评审实验结果
- 生成状态报告和最终报告包

## 当前使用方式

ResearchAgentTeam 当前主要在本地机器上运行，形态是可安装 Codex 插件加 Python 命令桥。它面向单个研究项目，核心工作界面是持续保存下来的文件和报告。
