from __future__ import annotations

from typing import Any, Callable, Optional, Tuple, Type

from research_agent_team.application.errors import CommandError
from research_agent_team.contracts.commands import ACTIVATION_COMMAND_NAMES, COMMAND_NAMES


CommandHandler = Callable[[dict[str, Any]], dict[str, Any]]


def ok(result: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "result": result}


def error(error_payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": False, "error": error_payload}


def not_implemented(kind: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "not_implemented",
            "kind": kind,
            "command": name,
            "message": f"{kind} '{name}' is defined for the plugin surface but is not implemented yet.",
        },
        "payload_keys": sorted(payload.keys()),
    }


def invalid_payload(message: str, **details: Any) -> dict[str, Any]:
    return error({"code": "invalid_payload", "message": message, **details})


def run_command(command_name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = _payload_or_empty(payload)
    handler, command_error = load_command_handler(command_name)
    if handler is None:
        return not_implemented("command", command_name, payload)
    assert command_error is not None
    try:
        return ok(handler(payload))
    except command_error as exc:
        return error(exc.to_dict())  # type: ignore[attr-defined]


def run_activation_callback(
    activation_command: str,
    root_path: str,
    activation_id: str,
    payload: dict[str, Any] | None = None,
    runtime_pid: int | None = None,
) -> dict[str, Any]:
    payload = _payload_or_empty(payload)
    try:
        if activation_command == "mark-running":
            from research_agent_team.application.activation_service import mark_activation_running

            return ok(mark_activation_running(root_path, activation_id, runtime_pid))
        if activation_command == "heartbeat":
            from research_agent_team.application.activation_service import heartbeat_activation

            return ok(heartbeat_activation(root_path, activation_id, payload or None))
        if activation_command == "checkpoint":
            from research_agent_team.application.activation_service import persist_checkpoint

            return ok(persist_checkpoint(root_path, activation_id, payload))
        if activation_command == "complete":
            from research_agent_team.application.activation_service import complete_activation

            return ok(complete_activation(root_path, activation_id, payload))
        if activation_command == "fail":
            from research_agent_team.application.activation_service import fail_activation

            return ok(fail_activation(root_path, activation_id, payload))
        if activation_command == "interrupt":
            from research_agent_team.application.activation_service import interrupt_activation

            return ok(interrupt_activation(root_path, activation_id, payload))
        if activation_command == "cancel":
            from research_agent_team.application.activation_service import cancel_activation

            return ok(cancel_activation(root_path, activation_id, payload))
        return not_implemented("activation", activation_command, payload)
    except CommandError as exc:
        return error(exc.to_dict())


def interpret_request(text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    from research_agent_team.application.command_interpreter_service import interpret_command

    try:
        interpretation = interpret_command(text, _payload_or_empty(context))
        return ok(interpretation.to_dict())
    except TypeError as exc:
        return invalid_payload(str(exc))


def plan_launches_for_result(
    root_path: str,
    command_result: dict[str, Any] | None = None,
    source_command: str | None = None,
    policy: str = "conservative",
) -> dict[str, Any]:
    from research_agent_team.runtime import plan_launches

    try:
        return ok(plan_launches(root_path, _payload_or_empty(command_result), source_command=source_command, policy=policy))
    except CommandError as exc:
        return error(exc.to_dict())


def render_launch_prompt_for_request(root_path: str, launch_request: dict[str, Any] | None = None) -> dict[str, Any]:
    from research_agent_team.runtime import render_launch_prompt

    try:
        return ok({"prompt": render_launch_prompt(root_path, _payload_or_empty(launch_request))})
    except CommandError as exc:
        return error(exc.to_dict())


def with_launch_plan(
    envelope: dict[str, Any],
    root_path: str | None,
    source_command: str | None,
    policy: str = "conservative",
) -> dict[str, Any]:
    result = dict(envelope)
    if not root_path:
        return result
    launch_plan = plan_launches_for_result(root_path, result, source_command=source_command, policy=policy)
    if launch_plan.get("ok"):
        result["launch_plan"] = launch_plan["result"]
    else:
        result["launch_plan_error"] = launch_plan["error"]
    return result


def root_path_from_payload(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    root_path = payload.get("root_path")
    return root_path if isinstance(root_path, str) and root_path.strip() else None


def load_command_handler(
    command_name: str,
) -> Tuple[Optional[CommandHandler], Optional[Type[Exception]]]:
    if command_name in {"create_project", "open_project", "switch_operating_mode", "pause_project", "resume_project"}:
        from research_agent_team.application.project_service import (
            create_project,
            open_project,
            pause_project,
            resume_project,
            switch_operating_mode,
        )

        return {
            "create_project": create_project,
            "open_project": open_project,
            "switch_operating_mode": switch_operating_mode,
            "pause_project": pause_project,
            "resume_project": resume_project,
        }[command_name], CommandError

    if command_name in {"show_team_topology", "add_senior", "add_junior", "retire_senior", "retire_junior"}:
        from research_agent_team.application.topology_service import (
            add_junior,
            add_senior,
            retire_junior,
            retire_senior,
            show_team_topology,
        )

        return {
            "show_team_topology": show_team_topology,
            "add_senior": add_senior,
            "add_junior": add_junior,
            "retire_senior": retire_senior,
            "retire_junior": retire_junior,
        }[command_name], CommandError

    if command_name == "assign_task":
        from research_agent_team.application.task_service import assign_task

        return assign_task, CommandError

    if command_name in {"approve_checkpoint", "reject_checkpoint"}:
        from research_agent_team.application.approval_service import approve_checkpoint, reject_checkpoint

        return {
            "approve_checkpoint": approve_checkpoint,
            "reject_checkpoint": reject_checkpoint,
        }[command_name], CommandError

    if command_name in {"request_status", "generate_report"}:
        from research_agent_team.application.reporting_service import generate_report, request_status

        return {
            "request_status": request_status,
            "generate_report": generate_report,
        }[command_name], CommandError

    if command_name == "sync_knowledge_base":
        from research_agent_team.application.knowledge_service import sync_knowledge_base

        return sync_knowledge_base, CommandError

    if command_name == "rebuild_graph":
        from research_agent_team.application.graph_service import rebuild_graph

        return rebuild_graph, CommandError

    if command_name in {"run_experiment", "review_experiment"}:
        from research_agent_team.application.experiment_service import review_experiment, run_experiment

        return {
            "run_experiment": run_experiment,
            "review_experiment": review_experiment,
        }[command_name], CommandError

    return None, None


def _payload_or_empty(payload: dict[str, Any] | None) -> dict[str, Any]:
    return dict(payload) if isinstance(payload, dict) else {}


__all__ = [
    "ACTIVATION_COMMAND_NAMES",
    "COMMAND_NAMES",
    "interpret_request",
    "load_command_handler",
    "plan_launches_for_result",
    "render_launch_prompt_for_request",
    "root_path_from_payload",
    "run_activation_callback",
    "run_command",
    "with_launch_plan",
]
