from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from research_agent_team.application.activation_service import _admit_next_task_if_possible_locked, _read_task, _write_task
from research_agent_team.application.errors import CommandError
from research_agent_team.application.project_service import _read_slot, _require_string, _write_slot, emit_event, load_project
from research_agent_team.domain import ApprovalApproverType, ApprovalScopeType, ApprovalStatus, TaskStatus
from research_agent_team.shared import new_id, now_utc
from research_agent_team.storage import ProjectLayout, project_lock, read_json, write_json_atomic


def _layout_from_payload(payload: Dict[str, Any]) -> ProjectLayout:
    return ProjectLayout(Path(_require_string(payload, "root_path")))


def approval_state_path(layout: ProjectLayout, approval_id: str) -> Path:
    if not approval_id or "/" in approval_id or "\\" in approval_id or approval_id in {".", ".."}:
        raise CommandError("invalid_payload", "approval_id is invalid", approval_id=approval_id)
    return layout.state_dir / "approvals" / f"{approval_id}.json"


def load_approval(layout: ProjectLayout, approval_id: str) -> Dict[str, Any]:
    path = approval_state_path(layout, approval_id)
    if not path.exists():
        raise CommandError("approval_not_found", f"Approval does not exist: {approval_id}", approval_id=approval_id)
    return read_json(path)


def write_approval(layout: ProjectLayout, approval: Dict[str, Any]) -> None:
    write_json_atomic(approval_state_path(layout, approval["approval_id"]), approval)


def approval_summary(approval: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "approval_id": approval["approval_id"],
        "scope_type": approval["scope_type"],
        "scope_id": approval["scope_id"],
        "approver_type": approval["approver_type"],
        "status": approval["status"],
        "reason": approval["reason"],
        "created_at": approval["created_at"],
    }


def create_approval(
    layout: ProjectLayout,
    *,
    scope_type: ApprovalScopeType,
    scope_id: str,
    requested_by_slot_id: str,
    approver_type: ApprovalApproverType,
    reason: str,
    request_payload: Dict[str, Any],
    task_id: Optional[str] = None,
    slot_id: Optional[str] = None,
) -> Dict[str, Any]:
    project = load_project(layout)
    timestamp = now_utc()
    approval = {
        "approval_id": new_id("approval"),
        "status": ApprovalStatus.PENDING.value,
        "scope_type": scope_type.value,
        "scope_id": scope_id,
        "requested_by_slot_id": requested_by_slot_id,
        "approver_type": approver_type.value,
        "reason": reason,
        "request_payload": dict(request_payload),
        "created_at": timestamp,
        "decided_at": None,
        "decision_summary": None,
    }
    write_approval(layout, approval)
    emit_event(
        layout,
        project.project_id,
        "approval.created",
        {"scope_type": scope_type.value, "scope_id": scope_id},
        slot_id=slot_id,
        task_id=task_id,
        approval_id=approval["approval_id"],
    )
    return approval


def _decision_summary(payload: Dict[str, Any]) -> str:
    value = payload.get("decision_summary", "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CommandError("invalid_payload", "decision_summary must be a string", field="decision_summary")
    return value


def _decide_budget_override(layout: ProjectLayout, approval: Dict[str, Any], approved: bool) -> Dict[str, Any]:
    project = load_project(layout)
    task = _read_task(layout, approval["scope_id"])
    slot = _read_slot(layout, task.owner_slot_id)
    timestamp = now_utc()
    task.current_approval_id = None
    task.updated_at = timestamp

    launch_request = None
    applied = approved
    if approved:
        task.status = TaskStatus.QUEUED
        task.block_reason = None
        slot.active_task_ids = [task_id for task_id in slot.active_task_ids if task_id != task.task_id]
        if task.task_id not in slot.queued_task_ids:
            slot.queued_task_ids.append(task.task_id)
        slot.updated_at = timestamp
        _write_slot(layout, slot)
        _write_task(layout, task)
        slot = _read_slot(layout, slot.slot_id)
        launch_request = _admit_next_task_if_possible_locked(layout, project.status, slot)
    else:
        task.status = TaskStatus.BLOCKED
        task.block_reason = "approval_rejected"
        slot.active_task_ids = [task_id for task_id in slot.active_task_ids if task_id != task.task_id]
        slot.queued_task_ids = [task_id for task_id in slot.queued_task_ids if task_id != task.task_id]
        slot.updated_at = timestamp
        _write_slot(layout, slot)
        _write_task(layout, task)
        applied = False

    return {
        "applied": applied,
        "affected_task_id": task.task_id,
        "affected_slot_id": slot.slot_id,
        "launch_request": launch_request,
        "generated_artifact": None,
    }


def _decide_generate_report(layout: ProjectLayout, approval: Dict[str, Any], approved: bool) -> Dict[str, Any]:
    if not approved:
        return {
            "applied": False,
            "affected_task_id": None,
            "affected_slot_id": None,
            "launch_request": None,
            "generated_artifact": None,
        }
    from research_agent_team.application.reporting_service import _generate_report_locked

    request_payload = approval.get("request_payload") or {}
    result = _generate_report_locked(
        layout,
        report_type=request_payload.get("report_type"),
        scope_type=request_payload.get("scope_type"),
        scope_id=request_payload.get("scope_id"),
        allow_pending_approval=False,
    )
    return {
        "applied": bool(result.get("generated")),
        "affected_task_id": None,
        "affected_slot_id": None,
        "launch_request": None,
        "generated_artifact": result.get("artifact"),
        "warnings": result.get("warnings", []),
    }


def _decide_staffing(layout: ProjectLayout, approval: Dict[str, Any], approved: bool) -> Dict[str, Any]:
    if not approved:
        return {
            "applied": False,
            "affected_task_id": None,
            "affected_slot_id": approval.get("scope_id"),
            "launch_request": None,
            "generated_artifact": None,
        }
    from research_agent_team.application.project_service import load_topology, read_all_slots
    from research_agent_team.application.topology_service import (
        _apply_add_junior_locked,
        _apply_add_senior_locked,
        _apply_retire_slot_locked,
    )
    from research_agent_team.domain import SlotRole

    project = load_project(layout)
    topology = load_topology(layout)
    slots = read_all_slots(layout)
    request_payload = approval.get("request_payload") or {}
    scope_type = approval.get("scope_type")
    if scope_type == ApprovalScopeType.ADD_SENIOR.value:
        result = _apply_add_senior_locked(layout, project, topology, slots, request_payload["preallocated_slot_id"])
    elif scope_type == ApprovalScopeType.ADD_JUNIOR.value:
        result = _apply_add_junior_locked(
            layout,
            project,
            topology,
            slots,
            request_payload["parent_slot_id"],
            request_payload["preallocated_slot_id"],
        )
    elif scope_type == ApprovalScopeType.RETIRE_SENIOR.value:
        result = _apply_retire_slot_locked(
            layout,
            project,
            topology,
            slots,
            request_payload["slot_id"],
            SlotRole.SENIOR_PHD,
            "senior_retired",
        )
    else:
        result = _apply_retire_slot_locked(
            layout,
            project,
            topology,
            slots,
            request_payload["slot_id"],
            SlotRole.JUNIOR_PHD,
            "junior_retired",
        )
    return {
        "applied": True,
        "affected_task_id": None,
        "affected_slot_id": result["slot"]["slot_id"],
        "launch_request": None,
        "generated_artifact": None,
    }


def _decide_checkpoint(payload: Dict[str, Any], *, approved: bool) -> Dict[str, Any]:
    layout = _layout_from_payload(payload)
    approval_id = _require_string(payload, "approval_id")
    decision_summary = _decision_summary(payload)
    with project_lock(layout.lock_path):
        approval = load_approval(layout, approval_id)
        if approval.get("status") != ApprovalStatus.PENDING.value:
            raise CommandError("approval_not_pending", "Only pending approvals can be decided", approval_id=approval_id)

        scope_type = approval.get("scope_type")
        warnings = []
        if scope_type == ApprovalScopeType.TASK_BUDGET_OVERRIDE.value:
            replay = _decide_budget_override(layout, approval, approved)
        elif scope_type == ApprovalScopeType.GENERATE_REPORT.value:
            replay = _decide_generate_report(layout, approval, approved)
            warnings.extend(replay.get("warnings", []))
        elif scope_type in {
            ApprovalScopeType.ADD_SENIOR.value,
            ApprovalScopeType.ADD_JUNIOR.value,
            ApprovalScopeType.RETIRE_SENIOR.value,
            ApprovalScopeType.RETIRE_JUNIOR.value,
        }:
            replay = _decide_staffing(layout, approval, approved)
        else:
            raise CommandError("unsupported_approval_scope", f"Unsupported approval scope: {scope_type}", scope_type=scope_type)

        timestamp = now_utc()
        approval["status"] = ApprovalStatus.APPROVED.value if approved else ApprovalStatus.REJECTED.value
        approval["decided_at"] = timestamp
        approval["decision_summary"] = decision_summary
        write_approval(layout, approval)

        project = load_project(layout)
        emit_event(
            layout,
            project.project_id,
            "approval.decided",
            {"status": approval["status"], "applied": bool(replay["applied"])},
            approval_id=approval["approval_id"],
            task_id=replay.get("affected_task_id"),
            slot_id=replay.get("affected_slot_id"),
        )

        from research_agent_team.application.reporting_service import rebuild_slot_views

        rebuild_slot_views(layout)
        return {
            "approval_id": approval["approval_id"],
            "status": approval["status"],
            "scope_type": approval["scope_type"],
            "scope_id": approval["scope_id"],
            "applied": bool(replay["applied"]),
            "affected_task_id": replay.get("affected_task_id"),
            "affected_slot_id": replay.get("affected_slot_id"),
            "launch_request": replay.get("launch_request"),
            "generated_artifact": replay.get("generated_artifact"),
            "warnings": warnings,
        }


def approve_checkpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _decide_checkpoint(payload, approved=True)


def reject_checkpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _decide_checkpoint(payload, approved=False)
