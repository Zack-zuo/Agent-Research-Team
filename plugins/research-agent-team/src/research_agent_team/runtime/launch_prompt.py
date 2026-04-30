from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any, Dict

from research_agent_team.application.errors import CommandError
from research_agent_team.application.project_service import _read_slot
from research_agent_team.domain import SlotRole
from research_agent_team.storage import ProjectLayout


def _required_string(payload: Dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CommandError("invalid_payload", f"{key} is required", field=key)
    return value.strip()


def _validate_project_relative_path(path_value: str) -> PurePosixPath:
    path = PurePosixPath(path_value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or "." in path.parts or ".." in path.parts:
        raise CommandError("invalid_launch_request", f"Launch request path escapes project root: {path_value}")
    return path


def _resolve_project_file(layout: ProjectLayout, project_relative_path: str) -> Path:
    relative_path = _validate_project_relative_path(project_relative_path)
    candidate = (layout.root / Path(*relative_path.parts)).resolve()
    if candidate != layout.root and layout.root not in candidate.parents:
        raise CommandError("invalid_launch_request", f"Launch request path escapes project root: {project_relative_path}")
    if not candidate.exists():
        raise CommandError("launch_input_not_found", f"Launch input does not exist: {project_relative_path}", path=project_relative_path)
    return candidate


def _role_label(role: SlotRole) -> str:
    if role == SlotRole.SENIOR_PHD:
        return "senior PhD activation"
    if role == SlotRole.JUNIOR_PHD:
        return "junior PhD activation"
    return f"{role.value} activation"


def render_launch_prompt(root_path: str, launch_request: Dict[str, Any]) -> str:
    layout = ProjectLayout(Path(root_path))
    activation_id = _required_string(launch_request, "activation_id")
    slot_id = _required_string(launch_request, "slot_id")
    task_id = _required_string(launch_request, "task_id")
    bundle_path = _required_string(launch_request, "bundle_path")
    briefing_path = _required_string(launch_request, "briefing_path")
    runtime_metadata_path = _required_string(launch_request, "runtime_metadata_path")
    permissions_manifest_path = launch_request.get("permissions_manifest_path")
    if permissions_manifest_path is not None and not isinstance(permissions_manifest_path, str):
        raise CommandError("invalid_launch_request", "permissions_manifest_path must be a string")

    slot = _read_slot(layout, slot_id)
    bundle_text = _resolve_project_file(layout, bundle_path).read_text(encoding="utf-8")
    briefing_text = _resolve_project_file(layout, briefing_path).read_text(encoding="utf-8")
    runtime_text = _resolve_project_file(layout, runtime_metadata_path).read_text(encoding="utf-8")
    permissions_text = None
    if permissions_manifest_path:
        permissions_text = _resolve_project_file(layout, permissions_manifest_path).read_text(encoding="utf-8")
    cli_name = "research-agent-team-codex"
    root_json = json.dumps(str(layout.root))
    complete_payload = json.dumps(json.dumps({"output_artifact_ids": [], "output_artifacts": []}))
    fail_payload = json.dumps(
        json.dumps(
            {
                "failure_summary": "Describe the failure cause and the next recovery step.",
                "output_artifact_ids": [],
                "output_artifacts": [],
            }
        )
    )
    checkpoint_payload = json.dumps(
        json.dumps(
            {
                "summary": "Describe durable progress.",
                "resume_instructions": "Describe exactly how to resume.",
                "output_artifact_ids": [],
                "output_artifacts": [],
            }
        )
    )

    return "\n".join(
        [
            f"You are a ResearchAgentTeam { _role_label(slot.role) } for slot `{slot_id}`.",
            f"Activation ID: `{activation_id}`.",
            f"Task ID: `{task_id}`.",
            "",
            "Use only the task bundle, briefing, and runtime metadata below as activation context.",
            "Read only paths allowed by the bundle. Write outputs under the slot workspace, slot KB, or explicitly allowed shared paths.",
            "",
            "Before doing work, mark the activation running:",
            f"`{cli_name} activation mark-running --root-path {root_json} --activation-id {activation_id}`",
            "",
            "While working, send heartbeat and checkpoint callbacks when useful:",
            f"`{cli_name} activation heartbeat --root-path {root_json} --activation-id {activation_id} --payload-json '{{}}'`",
            f"`{cli_name} activation checkpoint --root-path {root_json} --activation-id {activation_id} --payload-json {checkpoint_payload}`",
            "",
            "When work succeeds, create output files first, then complete the activation:",
            f"`{cli_name} activation complete --root-path {root_json} --activation-id {activation_id} --payload-json {complete_payload}`",
            "",
            "When work cannot succeed, fail the activation with a concrete failure summary:",
            f"`{cli_name} activation fail --root-path {root_json} --activation-id {activation_id} --payload-json {fail_payload}`",
            "",
            "Task briefing:",
            "```markdown",
            briefing_text,
            "```",
            "",
            "Task bundle:",
            "```json",
            bundle_text,
            "```",
            "",
            "Runtime metadata:",
            "```json",
            runtime_text,
            "```",
            "",
            "Permission manifest:",
            "```json",
            permissions_text or "{}",
            "```",
        ]
    )
