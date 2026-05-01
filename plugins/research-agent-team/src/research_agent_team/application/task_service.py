from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from research_agent_team.application.activation_service import _admit_next_task_if_possible_locked, _read_task, _write_task
from research_agent_team.application.errors import CommandError
from research_agent_team.application.policy_service import load_execution_policy
from research_agent_team.application.project_service import (
    _read_slot,
    _require_string,
    _slot_summary,
    _write_slot,
    emit_event,
    load_project,
    load_topology,
)
from research_agent_team.application.visibility_service import resolve_attached_artifacts
from research_agent_team.domain import AgentSlot, ApprovalApproverType, ApprovalScopeType, ApprovalStatus, ProjectStatus, SlotRole, SlotStatus, Task, TaskStatus
from research_agent_team.shared import new_id, now_utc
from research_agent_team.storage import ProjectLayout, project_lock, read_json, write_json_atomic


SUPPORTED_BUDGET_DIMENSIONS = ("token_budget", "wall_clock_seconds", "compute_units", "experiment_runs")


def _layout_from_payload(payload: Dict[str, Any]) -> ProjectLayout:
    return ProjectLayout(Path(_require_string(payload, "root_path")))


def _string_list(payload: Dict[str, Any], key: str, required: bool = False) -> List[str]:
    value = payload.get(key, [])
    if value is None:
        value = []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CommandError("invalid_payload", f"{key} must be a list of strings", field=key)
    cleaned = [item.strip() for item in value if item.strip()]
    if required and not cleaned:
        raise CommandError("invalid_payload", f"{key} must contain at least one non-empty string", field=key)
    return cleaned


def _optional_review_requirement(payload: Dict[str, Any]) -> str:
    value = payload.get("review_requirement", "none")
    if value not in {"none", "experiment_review"}:
        raise CommandError("invalid_payload", "Unsupported review_requirement", field="review_requirement")
    return value


def _budget_override(payload: Dict[str, Any]) -> Dict[str, float]:
    value = payload.get("budget_override", {})
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise CommandError("invalid_payload", "budget_override must be an object", field="budget_override")
    resolved: Dict[str, float] = {}
    for key, raw in value.items():
        if key not in SUPPORTED_BUDGET_DIMENSIONS:
            raise CommandError("invalid_payload", f"Unsupported budget dimension: {key}", field="budget_override")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
            raise CommandError("invalid_payload", "budget_override values must be positive numbers", field="budget_override")
        resolved[key] = float(raw)
    return resolved


def _validate_input_path_roots(layout: ProjectLayout, owner_slot_id: str, input_path_roots: List[str]) -> None:
    allowed_prefixes = [
        ("shared",),
        ("agents", owner_slot_id, "workspace"),
        ("agents", owner_slot_id, "kb"),
    ]
    for raw_path in input_path_roots:
        normalized = raw_path.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or "." in path.parts or ".." in path.parts or not path.parts:
            raise CommandError("invalid_path_root", "Input path roots must be project-relative", path=raw_path)
        if not any(path.parts[: len(prefix)] == prefix for prefix in allowed_prefixes):
            raise CommandError(
                "invalid_path_root",
                "Input path roots must target shared/, the owner workspace, or the owner KB",
                path=raw_path,
            )
        layout.project_relative_path(str(path))


def _requester_can_assign(requester: AgentSlot, owner: AgentSlot) -> bool:
    if requester.role == SlotRole.SUPERVISOR:
        return True
    if requester.slot_id == owner.slot_id:
        return True
    return owner.slot_id in requester.descendant_slot_ids


def _slot_is_executable(layout: ProjectLayout, slot: AgentSlot) -> bool:
    execution = load_execution_policy(layout)
    roles = execution.get("executable_roles")
    if isinstance(roles, list):
        return slot.role.value in roles
    role_policy = execution.get("roles")
    if isinstance(role_policy, dict):
        return bool((role_policy.get(slot.role.value) or {}).get("can_execute"))
    return slot.role in {SlotRole.SENIOR_PHD, SlotRole.JUNIOR_PHD}


def _coerce_budget_value(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _effective_budget_envelope(layout: ProjectLayout, owner: AgentSlot, budget_override: Dict[str, float]) -> Dict[str, float]:
    budget_state = read_json(layout.budget_state)
    budget_policy = read_json(layout.state_dir / "policies" / "budget.json")
    role_limits = budget_state.get("role_limits", {}).get(owner.role.value, {})
    default_task_budget = budget_policy.get("default_task_budget", {})
    resolved: Dict[str, float] = {}
    for dimension in SUPPORTED_BUDGET_DIMENSIONS:
        candidates = [
            _coerce_budget_value(budget_state.get("project_limits", {}).get(dimension)),
            _coerce_budget_value(role_limits.get(dimension) if isinstance(role_limits, dict) else None),
            _coerce_budget_value(default_task_budget.get(dimension) if isinstance(default_task_budget, dict) else None),
            _coerce_budget_value(budget_override.get(dimension)),
        ]
        defined = [candidate for candidate in candidates if candidate is not None]
        if defined:
            resolved[dimension] = min(defined)
    return resolved


def _queue_position(task_id: str, slot: AgentSlot) -> Optional[int]:
    if task_id not in slot.queued_task_ids:
        return None
    return slot.queued_task_ids.index(task_id) + 1


def _task_summary(task: Task, slot: AgentSlot) -> Dict[str, Any]:
    return {
        "task_id": task.task_id,
        "owner_slot_id": task.owner_slot_id,
        "status": task.status.value,
        "title": task.title,
        "queue_position": _queue_position(task.task_id, slot),
        "current_activation_id": task.current_activation_id,
        "latest_checkpoint_id": task.latest_checkpoint_id,
        "current_approval_id": task.current_approval_id,
    }


def _approval_summary(approval: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "approval_id": approval["approval_id"],
        "scope_type": approval["scope_type"],
        "scope_id": approval["scope_id"],
        "approver_type": approval["approver_type"],
        "status": approval["status"],
        "reason": approval["reason"],
        "created_at": approval["created_at"],
    }


def _write_pending_budget_approval(layout: ProjectLayout, task: Task, requester_slot_id: str, owner_slot_id: str) -> Dict[str, Any]:
    timestamp = now_utc()
    approval = {
        "approval_id": new_id("approval"),
        "status": ApprovalStatus.PENDING.value,
        "scope_type": ApprovalScopeType.TASK_BUDGET_OVERRIDE.value,
        "scope_id": task.task_id,
        "requested_by_slot_id": requester_slot_id,
        "approver_type": ApprovalApproverType.SUPERVISOR.value,
        "reason": "Budget override requires approval before task admission.",
        "request_payload": {"task_id": task.task_id, "owner_slot_id": owner_slot_id},
        "created_at": timestamp,
        "decided_at": None,
        "decision_summary": None,
    }
    write_json_atomic(layout.state_dir / "approvals" / f"{approval['approval_id']}.json", approval)
    return approval


def assign_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_payload(payload)
    requester_slot_id = _require_string(payload, "requester_slot_id")
    owner_slot_id = _require_string(payload, "owner_slot_id")
    title = _require_string(payload, "title")
    description = _require_string(payload, "description")
    success_criteria = _string_list(payload, "success_criteria")
    input_artifact_ids = _string_list(payload, "input_artifact_ids")
    input_path_roots = _string_list(payload, "input_path_roots")
    expected_output_types = _string_list(payload, "expected_output_types")
    budget_override = _budget_override(payload)
    review_requirement = _optional_review_requirement(payload)

    with project_lock(layout.lock_path):
        from research_agent_team.application.health_service import prepare_project_for_command_locked
        from research_agent_team.application.recovery_service import recover_stale_activations_locked

        preparation = prepare_project_for_command_locked(layout)
        project = load_project(layout)
        topology = load_topology(layout)
        recovery = recover_stale_activations_locked(layout, target_slot_ids={owner_slot_id})
        requester = _read_slot(layout, requester_slot_id)
        owner = _read_slot(layout, owner_slot_id)
        if project.status == ProjectStatus.ARCHIVED:
            raise CommandError("invalid_project_status", "Cannot assign tasks in an archived project", status=project.status.value)
        if requester.status != SlotStatus.ACTIVE or owner.status != SlotStatus.ACTIVE:
            raise CommandError("invalid_slot_status", "Requester and owner slots must both be active")
        if owner_slot_id not in topology.active_slot_ids:
            raise CommandError("invalid_slot_status", "Owner slot must be active in the current topology", slot_id=owner_slot_id)
        if not _requester_can_assign(requester, owner):
            raise CommandError("requester_not_authorized", "Requester cannot assign work to this owner slot")
        if not _slot_is_executable(layout, owner):
            raise CommandError("owner_not_executable", "Owner slot is not executable under policy", owner_slot_id=owner_slot_id)

        resolve_attached_artifacts(layout, requester_slot_id, owner_slot_id, input_artifact_ids)
        _validate_input_path_roots(layout, owner_slot_id, input_path_roots)
        budget_envelope = _effective_budget_envelope(layout, owner, budget_override)
        timestamp = now_utc()
        task = Task(
            task_id=new_id("task"),
            project_id=project.project_id,
            requester_slot_id=requester_slot_id,
            owner_slot_id=owner_slot_id,
            status=TaskStatus.QUEUED,
            title=title,
            description=description,
            success_criteria=success_criteria,
            input_artifact_ids=input_artifact_ids,
            input_path_roots=input_path_roots,
            expected_output_types=expected_output_types,
            budget_envelope=budget_envelope,
            review_requirement=review_requirement,
            created_at=timestamp,
            updated_at=timestamp,
            approval_policy_ref=project.policy_refs.get("approvals"),
        )

        pending_approval = None
        launch_request = None
        hook_warnings: List[str] = []
        budget_policy = read_json(layout.state_dir / "policies" / "budget.json")
        if budget_override and bool(budget_policy.get("requires_approval_for_override", True)):
            task.status = TaskStatus.AWAITING_APPROVAL
            _write_task(layout, task)
            approval = _write_pending_budget_approval(layout, task, requester_slot_id, owner_slot_id)
            task.current_approval_id = approval["approval_id"]
            _write_task(layout, task)
            if task.task_id not in owner.active_task_ids:
                owner.active_task_ids.append(task.task_id)
            owner.updated_at = timestamp
            _write_slot(layout, owner)
            pending_approval = _approval_summary(approval)
            hook_warnings.extend(
                emit_event(
                    layout,
                    project.project_id,
                    "task.awaiting_approval",
                    {"scope_type": ApprovalScopeType.TASK_BUDGET_OVERRIDE.value},
                    slot_id=owner.slot_id,
                    task_id=task.task_id,
                    approval_id=approval["approval_id"],
                    dispatch_hooks=True,
                )
            )
        else:
            _write_task(layout, task)
            owner.queued_task_ids.append(task.task_id)
            owner.updated_at = timestamp
            _write_slot(layout, owner)
            launch_request = _admit_next_task_if_possible_locked(layout, project.status, owner)

        hook_warnings.extend(
            emit_event(
                layout,
                project.project_id,
                "task.created",
                {"requester_slot_id": requester_slot_id, "title": title},
                slot_id=owner.slot_id,
                task_id=task.task_id,
                approval_id=task.current_approval_id,
                dispatch_hooks=True,
            )
        )

        from research_agent_team.application.reporting_service import rebuild_slot_views

        rebuild_slot_views(layout, {requester_slot_id, owner_slot_id, "supervisor"})
        task = _read_task(layout, task.task_id)
        owner = _read_slot(layout, owner.slot_id)
        return {
            "task": _task_summary(task, owner),
            "owner_slot": _slot_summary(owner),
            "admitted": launch_request is not None and task.status == TaskStatus.ADMITTED,
            "launch_request": launch_request,
            "owner_queue_depth": len(owner.queued_task_ids),
            "pending_approval": pending_approval,
            "recovery": recovery,
            "warnings": preparation.warnings + hook_warnings,
        }
