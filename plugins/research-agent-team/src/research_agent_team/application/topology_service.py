from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from research_agent_team.application.errors import CommandError
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
from research_agent_team.domain import AgentSlot, SlotRole, SlotStatus
from research_agent_team.shared import now_utc
from research_agent_team.storage import ProjectLayout, project_lock, write_json_atomic


def _layout_from_payload(payload: Dict[str, Any]) -> ProjectLayout:
    return ProjectLayout(Path(_require_string(payload, "root_path")))


def _slot_relative(slot_id: str, child: str) -> str:
    return f"agents/{slot_id}/{child}"


def _next_slot_id(slots: Dict[str, AgentSlot], prefix: str) -> str:
    highest = 0
    for slot_id in slots:
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


def _mutation_result(project: Any, topology: Any, slots: Dict[str, AgentSlot], slot: AgentSlot, warnings: List[str] = None) -> Dict[str, Any]:
    return {
        "project": _project_summary(project),
        "topology": _topology_summary(topology, slots),
        "slot": _slot_summary(slot),
        "warnings": list(warnings or []),
    }


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
        if len(active_seniors) >= int(staffing.get("max_active_seniors", 0)):
            raise CommandError("staffing_cap_exceeded", "Maximum active senior slots reached", role="senior_phd")

        timestamp = now_utc()
        slot = _new_slot(_next_slot_id(slots, "senior"), SlotRole.SENIOR_PHD, "supervisor", timestamp)
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
        if len(active_children) >= int(staffing.get("max_active_juniors_per_senior", 0)):
            raise CommandError("staffing_cap_exceeded", "Maximum active junior slots reached for senior", parent_slot_id=parent_slot_id)

        timestamp = now_utc()
        slot = _new_slot(_next_slot_id(slots, "junior"), SlotRole.JUNIOR_PHD, parent_slot_id, timestamp)
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


def _retire_slot(payload: Dict[str, Any], expected_role: SlotRole, command_name: str) -> Dict[str, Any]:
    slot_id = _require_string(payload, "slot_id")
    layout = _layout_from_payload(payload)
    if not layout.project_state.exists():
        load_project(layout)
    with project_lock(layout.lock_path):
        project = load_project(layout)
        topology = load_topology(layout)
        slots = read_all_slots(layout)
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


def retire_senior(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _retire_slot(payload, SlotRole.SENIOR_PHD, "senior_retired")


def retire_junior(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _retire_slot(payload, SlotRole.JUNIOR_PHD, "junior_retired")
