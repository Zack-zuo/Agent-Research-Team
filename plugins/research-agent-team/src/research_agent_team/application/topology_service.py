from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from research_agent_team.application.errors import CommandError
from research_agent_team.application.approval_service import approval_summary, create_approval
from research_agent_team.application.policy_service import load_staffing_policy
from research_agent_team.application.project_service import (
    _project_summary,
    _read_slot,
    _require_string,
    _slot_summary,
    _topology_summary,
    _write_slot,
    emit_event,
    load_project,
    load_topology,
    read_all_slots,
    write_topology,
)
from research_agent_team.config import default_knowledge_state
from research_agent_team.domain import AgentSlot, ApprovalApproverType, ApprovalScopeType, SlotRole, SlotStatus
from research_agent_team.shared import now_utc
from research_agent_team.storage import ProjectLayout, project_lock, read_json, write_json_atomic


def _layout_from_payload(payload: Dict[str, Any]) -> ProjectLayout:
    return ProjectLayout(Path(_require_string(payload, "root_path")))


def _slot_relative(slot_id: str, child: str) -> str:
    return f"agents/{slot_id}/{child}"


def _pending_approvals(layout: ProjectLayout) -> List[Dict[str, Any]]:
    approvals_dir = layout.state_dir / "approvals"
    if not approvals_dir.exists():
        return []
    approvals: List[Dict[str, Any]] = []
    for path in sorted(approvals_dir.glob("*.json")):
        approval = read_json(path)
        if approval.get("status") == "pending":
            approvals.append(approval)
    return approvals


def _pending_add_approvals(layout: ProjectLayout, scope_type: ApprovalScopeType, parent_slot_id: str = None) -> List[Dict[str, Any]]:
    approvals = [approval for approval in _pending_approvals(layout) if approval.get("scope_type") == scope_type.value]
    if parent_slot_id is None:
        return approvals
    return [
        approval
        for approval in approvals
        if (approval.get("request_payload") or {}).get("parent_slot_id") == parent_slot_id
    ]


def _reserved_slot_ids(layout: ProjectLayout) -> set[str]:
    reserved: set[str] = set()
    for approval in _pending_approvals(layout):
        if approval.get("scope_type") not in {ApprovalScopeType.ADD_SENIOR.value, ApprovalScopeType.ADD_JUNIOR.value}:
            continue
        slot_id = (approval.get("request_payload") or {}).get("preallocated_slot_id")
        if isinstance(slot_id, str) and slot_id.strip():
            reserved.add(slot_id)
    return reserved


def _next_slot_id(slots: Dict[str, AgentSlot], prefix: str, reserved_slot_ids: set[str] = None) -> str:
    highest = 0
    for slot_id in set(slots) | set(reserved_slot_ids or set()):
        if slot_id.startswith(prefix + "-"):
            suffix = slot_id.rsplit("-", 1)[-1]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    return f"{prefix}-{highest + 1:02d}"


def _new_slot(slot_id: str, role: SlotRole, parent_slot_id: str, timestamp: str) -> AgentSlot:
    return AgentSlot(
        slot_id=slot_id,
        role=role,
        parent_slot_id=parent_slot_id,
        status=SlotStatus.ACTIVE,
        created_at=timestamp,
        updated_at=timestamp,
        workspace_root=_slot_relative(slot_id, "workspace"),
        kb_root=_slot_relative(slot_id, "kb"),
        inbox_root=_slot_relative(slot_id, "inbox"),
        outbox_root=_slot_relative(slot_id, "outbox"),
    )


def _active_slots_with_role(slots: Dict[str, AgentSlot], role: SlotRole) -> List[AgentSlot]:
    return [slot for slot in slots.values() if slot.role == role and slot.status == SlotStatus.ACTIVE]


def _recompute_descendants(slots: Dict[str, AgentSlot], children_by_slot_id: Dict[str, List[str]]) -> None:
    def descendants(slot_id: str) -> List[str]:
        found: List[str] = []
        for child_id in children_by_slot_id.get(slot_id, []):
            child = slots.get(child_id)
            if child is None or child.status != SlotStatus.ACTIVE:
                continue
            found.append(child_id)
            found.extend(descendants(child_id))
        return found

    for slot in slots.values():
        slot.descendant_slot_ids = descendants(slot.slot_id)


def _write_topology_state(layout: ProjectLayout, topology: Any, slots: Dict[str, AgentSlot], timestamp: str) -> None:
    topology.generation += 1
    topology.updated_at = timestamp
    _recompute_descendants(slots, topology.children_by_slot_id)
    for slot in slots.values():
        slot.updated_at = timestamp
        _write_slot(layout, slot)
    write_topology(layout, topology)


def _mutation_result(
    project: Any,
    topology: Any,
    slots: Dict[str, AgentSlot],
    slot: AgentSlot,
    warnings: List[str] = None,
    pending_approval: Dict[str, Any] = None,
) -> Dict[str, Any]:
    return {
        "project": _project_summary(project),
        "topology": _topology_summary(topology, slots),
        "slot": _slot_summary(slot),
        "pending_approval": pending_approval,
        "warnings": list(warnings or []),
    }


def _staffing_requires_approval(staffing: Dict[str, Any]) -> bool:
    return bool(staffing.get("require_staffing_approval"))


def _pending_staffing_result(
    layout: ProjectLayout,
    project: Any,
    topology: Any,
    slots: Dict[str, AgentSlot],
    slot: AgentSlot,
    scope_type: ApprovalScopeType,
    reason: str,
    request_payload: Dict[str, Any],
) -> Dict[str, Any]:
    approval = create_approval(
        layout,
        scope_type=scope_type,
        scope_id=slot.slot_id,
        requested_by_slot_id="supervisor",
        approver_type=ApprovalApproverType.SUPERVISOR,
        reason=reason,
        request_payload=request_payload,
        slot_id=slot.slot_id,
    )
    return _mutation_result(project, topology, slots, slot, pending_approval=approval_summary(approval))


def _apply_add_senior_locked(layout: ProjectLayout, project: Any, topology: Any, slots: Dict[str, AgentSlot], slot_id: str) -> Dict[str, Any]:
    staffing = load_staffing_policy(layout)
    if len(_active_slots_with_role(slots, SlotRole.SENIOR_PHD)) >= int(staffing.get("max_active_seniors", 0)):
        raise CommandError("staffing_cap_exceeded", "Maximum active senior slots reached", role="senior_phd")
    if slot_id in slots or slot_id in topology.parent_by_slot_id or slot_id in topology.active_slot_ids or slot_id in topology.retired_slot_ids:
        raise CommandError("staffing_slot_id_conflict", f"Slot ID is no longer available: {slot_id}", slot_id=slot_id)
    timestamp = now_utc()
    slot = _new_slot(slot_id, SlotRole.SENIOR_PHD, "supervisor", timestamp)
    slots[slot.slot_id] = slot
    topology.parent_by_slot_id[slot.slot_id] = "supervisor"
    topology.children_by_slot_id.setdefault("supervisor", []).append(slot.slot_id)
    topology.children_by_slot_id[slot.slot_id] = []
    topology.active_slot_ids.append(slot.slot_id)
    layout.create_slot_layout(slot.slot_id)
    write_json_atomic(layout.state_dir / "knowledge" / "slots" / f"{slot.slot_id}.json", default_knowledge_state("slot", slot.slot_id))
    _write_topology_state(layout, topology, slots, timestamp)
    emit_event(layout, project.project_id, "topology.senior_added", {"slot_id": slot.slot_id}, slot_id=slot.slot_id)
    return _mutation_result(project, topology, slots, slot)


def _apply_add_junior_locked(
    layout: ProjectLayout,
    project: Any,
    topology: Any,
    slots: Dict[str, AgentSlot],
    parent_slot_id: str,
    slot_id: str,
) -> Dict[str, Any]:
    if slot_id in slots or slot_id in topology.parent_by_slot_id or slot_id in topology.active_slot_ids or slot_id in topology.retired_slot_ids:
        raise CommandError("staffing_slot_id_conflict", f"Slot ID is no longer available: {slot_id}", slot_id=slot_id)
    parent = slots.get(parent_slot_id)
    if parent is None:
        raise CommandError("slot_not_found", f"Slot does not exist: {parent_slot_id}", slot_id=parent_slot_id)
    if parent.role != SlotRole.SENIOR_PHD:
        raise CommandError("invalid_parent_role", "Junior slots require an active senior parent", parent_slot_id=parent_slot_id)
    if parent.status != SlotStatus.ACTIVE:
        raise CommandError("invalid_parent_status", "Junior slots require an active senior parent", parent_slot_id=parent_slot_id)
    staffing = load_staffing_policy(layout)
    active_children = [
        slots[child_id]
        for child_id in topology.children_by_slot_id.get(parent_slot_id, [])
        if child_id in slots and slots[child_id].status == SlotStatus.ACTIVE and slots[child_id].role == SlotRole.JUNIOR_PHD
    ]
    if len(active_children) >= int(staffing.get("max_active_juniors_per_senior", 0)):
        raise CommandError("staffing_cap_exceeded", "Maximum active junior slots reached for senior", parent_slot_id=parent_slot_id)
    timestamp = now_utc()
    slot = _new_slot(slot_id, SlotRole.JUNIOR_PHD, parent_slot_id, timestamp)
    slots[slot.slot_id] = slot
    topology.parent_by_slot_id[slot.slot_id] = parent_slot_id
    topology.children_by_slot_id.setdefault(parent_slot_id, []).append(slot.slot_id)
    topology.children_by_slot_id[slot.slot_id] = []
    topology.active_slot_ids.append(slot.slot_id)
    layout.create_slot_layout(slot.slot_id)
    write_json_atomic(layout.state_dir / "knowledge" / "slots" / f"{slot.slot_id}.json", default_knowledge_state("slot", slot.slot_id))
    _write_topology_state(layout, topology, slots, timestamp)
    emit_event(layout, project.project_id, "topology.junior_added", {"slot_id": slot.slot_id, "parent_slot_id": parent_slot_id}, slot_id=slot.slot_id)
    return _mutation_result(project, topology, slots, slot)


def _validate_retirement(slots: Dict[str, AgentSlot], topology: Any, slot_id: str, expected_role: SlotRole, command_name: str) -> AgentSlot:
    slot = slots.get(slot_id)
    if slot is None:
        raise CommandError("slot_not_found", f"Slot does not exist: {slot_id}", slot_id=slot_id)
    if slot.role != expected_role:
        raise CommandError("invalid_slot_role", f"{command_name} cannot retire role {slot.role.value}", slot_id=slot_id)
    if slot.status != SlotStatus.ACTIVE:
        raise CommandError("invalid_slot_status", "Only active slots can be retired", slot_id=slot_id)
    if slot.current_activation_id or slot.active_task_ids or slot.queued_task_ids:
        raise CommandError("slot_has_work", "Slot has active or queued work", slot_id=slot_id)
    if expected_role == SlotRole.SENIOR_PHD:
        active_children = [
            child_id
            for child_id in topology.children_by_slot_id.get(slot_id, [])
            if child_id in slots and slots[child_id].status == SlotStatus.ACTIVE
        ]
        if active_children:
            raise CommandError("active_child_slots", "Senior has active child slots", slot_id=slot_id, child_slot_ids=active_children)
    return slot


def _apply_retire_slot_locked(
    layout: ProjectLayout,
    project: Any,
    topology: Any,
    slots: Dict[str, AgentSlot],
    slot_id: str,
    expected_role: SlotRole,
    command_name: str,
) -> Dict[str, Any]:
    slot = _validate_retirement(slots, topology, slot_id, expected_role, command_name)
    timestamp = now_utc()
    slot.status = SlotStatus.RETIRED
    slot.retired_at = timestamp
    slot.updated_at = timestamp
    topology.active_slot_ids = [active_id for active_id in topology.active_slot_ids if active_id != slot_id]
    if slot_id not in topology.retired_slot_ids:
        topology.retired_slot_ids.append(slot_id)
    _write_topology_state(layout, topology, slots, timestamp)
    emit_event(layout, project.project_id, f"topology.{command_name}", {"slot_id": slot_id}, slot_id=slot_id)
    return _mutation_result(project, topology, slots, slot)


def show_team_topology(payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_payload(payload)
    project = load_project(layout)
    topology = load_topology(layout)
    slots = read_all_slots(layout)
    return {"project": _project_summary(project), "topology": _topology_summary(topology, slots), "warnings": []}


def add_senior(payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_payload(payload)
    if not layout.project_state.exists():
        load_project(layout)
    with project_lock(layout.lock_path):
        project = load_project(layout)
        topology = load_topology(layout)
        slots = read_all_slots(layout)
        staffing = load_staffing_policy(layout)
        active_seniors = _active_slots_with_role(slots, SlotRole.SENIOR_PHD)
        pending_seniors = _pending_add_approvals(layout, ApprovalScopeType.ADD_SENIOR)
        if len(active_seniors) + len(pending_seniors) >= int(staffing.get("max_active_seniors", 0)):
            raise CommandError("staffing_cap_exceeded", "Maximum active senior slots reached", role="senior_phd")

        slot_id = _next_slot_id(slots, "senior", _reserved_slot_ids(layout))
        if _staffing_requires_approval(staffing):
            slot = _new_slot(slot_id, SlotRole.SENIOR_PHD, "supervisor", now_utc())
            return _pending_staffing_result(
                layout,
                project,
                topology,
                slots,
                slot,
                ApprovalScopeType.ADD_SENIOR,
                "Staffing approval required before adding senior slot.",
                {"preallocated_slot_id": slot_id},
            )
        return _apply_add_senior_locked(layout, project, topology, slots, slot_id)


def add_junior(payload: Dict[str, Any]) -> Dict[str, Any]:
    parent_slot_id = _require_string(payload, "parent_slot_id")
    layout = _layout_from_payload(payload)
    if not layout.project_state.exists():
        load_project(layout)
    with project_lock(layout.lock_path):
        project = load_project(layout)
        topology = load_topology(layout)
        slots = read_all_slots(layout)
        staffing = load_staffing_policy(layout)
        parent = slots.get(parent_slot_id)
        if parent is None:
            raise CommandError("slot_not_found", f"Slot does not exist: {parent_slot_id}", slot_id=parent_slot_id)
        if parent.role != SlotRole.SENIOR_PHD:
            raise CommandError("invalid_parent_role", "Junior slots require an active senior parent", parent_slot_id=parent_slot_id)
        if parent.status != SlotStatus.ACTIVE:
            raise CommandError("invalid_parent_status", "Junior slots require an active senior parent", parent_slot_id=parent_slot_id)

        active_children = [
            slots[child_id]
            for child_id in topology.children_by_slot_id.get(parent_slot_id, [])
            if child_id in slots and slots[child_id].status == SlotStatus.ACTIVE and slots[child_id].role == SlotRole.JUNIOR_PHD
        ]
        pending_children = _pending_add_approvals(layout, ApprovalScopeType.ADD_JUNIOR, parent_slot_id)
        if len(active_children) + len(pending_children) >= int(staffing.get("max_active_juniors_per_senior", 0)):
            raise CommandError("staffing_cap_exceeded", "Maximum active junior slots reached for senior", parent_slot_id=parent_slot_id)

        slot_id = _next_slot_id(slots, "junior", _reserved_slot_ids(layout))
        if _staffing_requires_approval(staffing):
            slot = _new_slot(slot_id, SlotRole.JUNIOR_PHD, parent_slot_id, now_utc())
            return _pending_staffing_result(
                layout,
                project,
                topology,
                slots,
                slot,
                ApprovalScopeType.ADD_JUNIOR,
                "Staffing approval required before adding junior slot.",
                {"parent_slot_id": parent_slot_id, "preallocated_slot_id": slot_id},
            )
        return _apply_add_junior_locked(layout, project, topology, slots, parent_slot_id, slot_id)


def _retire_slot(payload: Dict[str, Any], expected_role: SlotRole, command_name: str) -> Dict[str, Any]:
    slot_id = _require_string(payload, "slot_id")
    layout = _layout_from_payload(payload)
    if not layout.project_state.exists():
        load_project(layout)
    with project_lock(layout.lock_path):
        project = load_project(layout)
        topology = load_topology(layout)
        slots = read_all_slots(layout)
        slot = _validate_retirement(slots, topology, slot_id, expected_role, command_name)
        staffing = load_staffing_policy(layout)
        if _staffing_requires_approval(staffing):
            scope_type = ApprovalScopeType.RETIRE_SENIOR if expected_role == SlotRole.SENIOR_PHD else ApprovalScopeType.RETIRE_JUNIOR
            return _pending_staffing_result(
                layout,
                project,
                topology,
                slots,
                slot,
                scope_type,
                f"Staffing approval required before retiring {slot_id}.",
                {"slot_id": slot_id},
            )
        return _apply_retire_slot_locked(layout, project, topology, slots, slot_id, expected_role, command_name)


def retire_senior(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _retire_slot(payload, SlotRole.SENIOR_PHD, "senior_retired")


def retire_junior(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _retire_slot(payload, SlotRole.JUNIOR_PHD, "junior_retired")
