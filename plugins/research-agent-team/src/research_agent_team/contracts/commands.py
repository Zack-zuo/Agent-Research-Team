"""Public command names shared by Codex-facing adapters and docs."""

PROJECT_COMMANDS: tuple[str, ...] = (
    "create_project",
    "open_project",
    "switch_operating_mode",
    "pause_project",
    "resume_project",
)

TOPOLOGY_COMMANDS: tuple[str, ...] = (
    "show_team_topology",
    "add_senior",
    "add_junior",
    "retire_senior",
    "retire_junior",
)

WORKFLOW_COMMANDS: tuple[str, ...] = (
    "assign_task",
    "run_experiment",
    "review_experiment",
    "approve_checkpoint",
    "reject_checkpoint",
    "request_status",
    "generate_report",
    "sync_knowledge_base",
    "rebuild_graph",
)

COMMAND_NAMES: tuple[str, ...] = PROJECT_COMMANDS + TOPOLOGY_COMMANDS + WORKFLOW_COMMANDS

ACTIVATION_COMMAND_NAMES: tuple[str, ...] = (
    "mark-running",
    "heartbeat",
    "checkpoint",
    "complete",
    "fail",
)
