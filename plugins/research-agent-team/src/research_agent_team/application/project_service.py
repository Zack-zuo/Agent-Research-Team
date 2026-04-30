from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml

from research_agent_team.application.errors import CommandError
from research_agent_team.config import (
    default_adapter_config,
    default_adapter_health,
    default_budget_state,
    default_hook_config,
    default_knowledge_state,
    default_policy_files,
)
from research_agent_team.domain import AgentSlot, Event, OperatingMode, ProjectStatus, ResearchProject, SlotRole, SlotStatus, TeamTopology
from research_agent_team.shared import new_id, now_utc, utc_date
from research_agent_team.storage import (
    ProjectLayout,
    SCHEMA_VERSION,
    append_jsonl,
    ensure_support_surfaces,
    project_lock,
    read_json,
    write_json_atomic,
    write_text_atomic,
    write_yaml_atomic,
)


def _require_string(payload: Dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CommandError("invalid_payload", f"{key} is required", field=key)
    return value.strip()


def _layout_from_payload(payload: Dict[str, Any]) -> ProjectLayout:
    return ProjectLayout(Path(_require_string(payload, "root_path")))


def _slot_relative(slot_id: str, child: str) -> str:
    return f"agents/{slot_id}/{child}"


def _event_path(layout: ProjectLayout, timestamp: str) -> Path:
    return layout.events_dir / f"{utc_date(timestamp)}.jsonl"


def emit_event(
    layout: ProjectLayout,
    project_id: str,
    event_type: str,
    payload: Dict[str, Any],
    slot_id: str = None,
    task_id: str = None,
    activation_id: str = None,
    approval_id: str = None,
) -> None:
    timestamp = now_utc()
    event = Event(
        event_id=new_id("event"),
        event_type=event_type,
        created_at=timestamp,
        project_id=project_id,
        slot_id=slot_id,
        task_id=task_id,
        activation_id=activation_id,
        approval_id=approval_id,
        payload=payload,
    )
    append_jsonl(_event_path(layout, timestamp), event.to_dict())


def _write_slot(layout: ProjectLayout, slot: AgentSlot) -> None:
    write_json_atomic(layout.slot_state_path(slot.slot_id), slot.to_dict())


def _read_slot(layout: ProjectLayout, slot_id: str) -> AgentSlot:
    path = layout.slot_state_path(slot_id)
    if not path.exists():
        raise CommandError("slot_not_found", f"Slot does not exist: {slot_id}", slot_id=slot_id)
    return AgentSlot.from_dict(read_json(path))


def read_all_slots(layout: ProjectLayout) -> Dict[str, AgentSlot]:
    slots: Dict[str, AgentSlot] = {}
    for path in sorted((layout.state_dir / "slots").glob("*.json")):
        slot = AgentSlot.from_dict(read_json(path))
        slots[slot.slot_id] = slot
    return slots


def _write_project(layout: ProjectLayout, project: ResearchProject) -> None:
    write_json_atomic(layout.project_state, project.to_dict())
    write_yaml_atomic(
        layout.project_manifest,
        {
            "project_id": project.project_id,
            "name": project.name,
            "schema_version": project.schema_version,
            "root_path": project.root_path,
            "status": project.status.value,
            "operating_mode": project.operating_mode.value,
            "supervisor_slot_id": project.supervisor_slot_id,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
        },
    )


def load_project(layout: ProjectLayout) -> ResearchProject:
    if not layout.project_state.exists():
        raise CommandError("project_not_found", "Project state does not exist", root_path=str(layout.root))
    project = ResearchProject.from_dict(read_json(layout.project_state))
    if project.schema_version != SCHEMA_VERSION:
        raise CommandError(
            "unsupported_schema_version",
            f"Unsupported schema version: {project.schema_version}",
            expected_schema_version=SCHEMA_VERSION,
            actual_schema_version=project.schema_version,
        )
    return project


def load_topology(layout: ProjectLayout) -> TeamTopology:
    return TeamTopology.from_dict(read_json(layout.topology_state))


def write_topology(layout: ProjectLayout, topology: TeamTopology) -> None:
    write_json_atomic(layout.topology_state, topology.to_dict())


def _project_summary(project: ResearchProject) -> Dict[str, Any]:
    return {
        "project_id": project.project_id,
        "name": project.name,
        "schema_version": project.schema_version,
        "status": project.status.value,
        "operating_mode": project.operating_mode.value,
        "root_path": project.root_path,
        "supervisor_slot_id": project.supervisor_slot_id,
        "updated_at": project.updated_at,
    }


def _slot_summary(slot: AgentSlot) -> Dict[str, Any]:
    return {
        "slot_id": slot.slot_id,
        "role": slot.role.value,
        "parent_slot_id": slot.parent_slot_id,
        "status": slot.status.value,
        "current_activation_id": slot.current_activation_id,
        "queued_task_count": len(slot.queued_task_ids),
        "active_task_count": len(slot.active_task_ids),
        "descendant_slot_ids": list(slot.descendant_slot_ids),
    }


def _topology_summary(topology: TeamTopology, slots: Dict[str, AgentSlot]) -> Dict[str, Any]:
    return {
        "generation": topology.generation,
        "supervisor_slot_id": topology.supervisor_slot_id,
        "active_slot_ids": list(topology.active_slot_ids),
        "retired_slot_ids": list(topology.retired_slot_ids),
        "slot_count": len(slots),
        "slots": [_slot_summary(slots[slot_id]) for slot_id in topology.active_slot_ids + topology.retired_slot_ids if slot_id in slots],
        "children_by_slot_id": {key: list(value) for key, value in topology.children_by_slot_id.items()},
    }


def _pending_approval_count(layout: ProjectLayout) -> int:
    count = 0
    for path in (layout.state_dir / "approvals").glob("*.json"):
        if read_json(path).get("status") == "pending":
            count += 1
    return count


def _task_counts(layout: ProjectLayout) -> Dict[str, int]:
    active_statuses = {"admitted", "running", "awaiting_review", "awaiting_approval", "blocked"}
    counts = {"active": 0, "queued": 0}
    for path in (layout.state_dir / "tasks").glob("*.json"):
        status = read_json(path).get("status")
        if status == "queued":
            counts["queued"] += 1
        elif status in active_statuses:
            counts["active"] += 1
    return counts


def _recent_artifacts(layout: ProjectLayout, limit: int = 10) -> List[Dict[str, Any]]:
    if not layout.artifact_index.exists():
        return []
    lines = [line for line in layout.artifact_index.read_text(encoding="utf-8").splitlines() if line.strip()]
    artifacts: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            artifacts.append(yaml.safe_load(line))
        except yaml.YAMLError:
            continue
    return artifacts


def _open_result(layout: ProjectLayout, project: ResearchProject, warnings: List[str], recovery: Dict[str, int] = None) -> Dict[str, Any]:
    topology = load_topology(layout)
    slots = read_all_slots(layout)
    task_counts = _task_counts(layout)
    adapter_health = read_json(layout.adapter_health) if layout.adapter_health.exists() else {}
    return {
        "project": _project_summary(project),
        "topology": _topology_summary(topology, slots),
        "warnings": warnings,
        "active_work_count": task_counts["active"],
        "queued_work_count": task_counts["queued"],
        "pending_approval_count": _pending_approval_count(layout),
        "recent_artifacts": _recent_artifacts(layout),
        "adapter_health": adapter_health,
        "recovery": recovery or {"recovered_activation_count": 0, "requeued_task_count": 0, "blocked_task_count": 0},
    }


def _initial_slot(slot_id: str, role: SlotRole, parent_slot_id: str, timestamp: str) -> AgentSlot:
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


def create_project(payload: Dict[str, Any]) -> Dict[str, Any]:
    name = _require_string(payload, "name")
    layout = _layout_from_payload(payload)
    initial_charter_text = payload.get("initial_charter_text", "")
    if not isinstance(initial_charter_text, str):
        raise CommandError("invalid_payload", "initial_charter_text must be a string", field="initial_charter_text")

    if layout.root.exists() and not layout.root.is_dir():
        raise CommandError("invalid_root_path", "create_project root_path must be a directory", root_path=str(layout.root))
    if layout.root.exists() and any(layout.root.iterdir()):
        raise CommandError("target_not_empty", "create_project requires an empty or missing root_path", root_path=str(layout.root))

    layout.create_base_layout()
    timestamp = now_utc()
    project_id = new_id("project")
    project = ResearchProject(
        project_id=project_id,
        name=name,
        schema_version=SCHEMA_VERSION,
        status=ProjectStatus.ACTIVE,
        operating_mode=OperatingMode.AUTONOMOUS,
        root_path=str(layout.root),
        created_at=timestamp,
        updated_at=timestamp,
        supervisor_slot_id="supervisor",
        policy_refs={
            "staffing": "state/policies/staffing.json",
            "execution": "state/policies/execution.json",
            "budget": "state/policies/budget.json",
            "approvals": "state/policies/approvals.json",
        },
    )

    supervisor = _initial_slot("supervisor", SlotRole.SUPERVISOR, None, timestamp)
    supervisor.descendant_slot_ids = ["senior-01"]
    senior = _initial_slot("senior-01", SlotRole.SENIOR_PHD, "supervisor", timestamp)
    topology = TeamTopology(
        project_id=project_id,
        generation=0,
        supervisor_slot_id="supervisor",
        parent_by_slot_id={"supervisor": None, "senior-01": "supervisor"},
        children_by_slot_id={"supervisor": ["senior-01"], "senior-01": []},
        active_slot_ids=["supervisor", "senior-01"],
        retired_slot_ids=[],
        updated_at=timestamp,
    )

    _write_project(layout, project)
    _write_slot(layout, supervisor)
    _write_slot(layout, senior)
    write_topology(layout, topology)
    for slot_id in topology.active_slot_ids:
        layout.create_slot_layout(slot_id)
        write_json_atomic(layout.state_dir / "knowledge" / "slots" / f"{slot_id}.json", default_knowledge_state("slot", slot_id))

    policies = default_policy_files()
    for filename, policy in policies.items():
        write_json_atomic(layout.state_dir / "policies" / filename, policy)
    budget = default_budget_state()
    budget["last_recomputed_at"] = timestamp
    write_json_atomic(layout.budget_state, budget)
    write_json_atomic(layout.adapter_config, default_adapter_config())
    write_json_atomic(layout.adapter_health, default_adapter_health())
    write_json_atomic(layout.hook_config, default_hook_config())
    write_json_atomic(layout.state_dir / "knowledge" / "project.json", default_knowledge_state("project", project_id))

    charter_text = initial_charter_text.strip() or f"# {name}\n\nInitial research charter.\n"
    if not charter_text.endswith("\n"):
        charter_text += "\n"
    charter_path = layout.root / "shared" / "artifacts" / "initial-charter.md"
    write_text_atomic(charter_path, charter_text)
    append_jsonl(
        layout.artifact_index,
        {
            "artifact_id": new_id("artifact"),
            "type": "initial_charter",
            "path": "shared/artifacts/initial-charter.md",
            "visibility": "project_shared",
            "producing_slot_id": "supervisor",
            "created_at": timestamp,
        },
    )
    emit_event(layout, project_id, "project.created", {"name": name})
    emit_event(layout, project_id, "topology.slot_created", {"slot_id": "supervisor"}, slot_id="supervisor")
    emit_event(layout, project_id, "topology.slot_created", {"slot_id": "senior-01"}, slot_id="senior-01")

    return {
        "project": _project_summary(project),
        "topology": _topology_summary(topology, {"supervisor": supervisor, "senior-01": senior}),
        "warnings": [],
    }


def open_project(payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_payload(payload)
    if not layout.project_state.exists():
        load_project(layout)
    with project_lock(layout.lock_path):
        from research_agent_team.application.recovery_service import recover_stale_activations_locked

        project = load_project(layout)
        topology = load_topology(layout)
        warnings = ensure_support_surfaces(layout, topology.active_slot_ids + topology.retired_slot_ids)
        recovery = recover_stale_activations_locked(layout)
        from research_agent_team.application.reporting_service import rebuild_slot_views

        rebuild_slot_views(layout)
        return _open_result(layout, project, warnings, recovery)


def switch_operating_mode(payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_payload(payload)
    requested = _require_string(payload, "operating_mode")
    try:
        operating_mode = OperatingMode(requested)
    except ValueError:
        raise CommandError("invalid_operating_mode", f"Unsupported operating mode: {requested}", operating_mode=requested)
    if not layout.project_state.exists():
        load_project(layout)
    with project_lock(layout.lock_path):
        project = load_project(layout)
        project.operating_mode = operating_mode
        project.updated_at = now_utc()
        _write_project(layout, project)
        emit_event(layout, project.project_id, "project.mode_switched", {"operating_mode": operating_mode.value})
        return {"project": _project_summary(project), "warnings": []}


def pause_project(payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_payload(payload)
    if not layout.project_state.exists():
        load_project(layout)
    with project_lock(layout.lock_path):
        from research_agent_team.application.recovery_service import recover_stale_activations_locked

        project = load_project(layout)
        topology = load_topology(layout)
        warnings = ensure_support_surfaces(layout, topology.active_slot_ids + topology.retired_slot_ids)
        recovery = recover_stale_activations_locked(layout)
        project.status = ProjectStatus.PAUSED
        project.updated_at = now_utc()
        _write_project(layout, project)
        emit_event(layout, project.project_id, "project.paused", {})
        return {"project": _project_summary(project), "recovery": recovery, "warnings": warnings}


def resume_project(payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_payload(payload)
    if not layout.project_state.exists():
        load_project(layout)
    with project_lock(layout.lock_path):
        from research_agent_team.application.activation_service import _admit_next_task_if_possible_locked
        from research_agent_team.application.recovery_service import recover_stale_activations_locked

        project = load_project(layout)
        topology = load_topology(layout)
        warnings = ensure_support_surfaces(layout, topology.active_slot_ids + topology.retired_slot_ids)
        recovery = recover_stale_activations_locked(layout)
        project.status = ProjectStatus.ACTIVE
        project.updated_at = now_utc()
        _write_project(layout, project)
        launch_requests = []
        for slot_id in topology.active_slot_ids:
            slot = _read_slot(layout, slot_id)
            launch_request = _admit_next_task_if_possible_locked(layout, project.status, slot)
            if launch_request is not None:
                launch_requests.append(launch_request)
        emit_event(layout, project.project_id, "project.resumed", {"admitted_task_count": len(launch_requests)})
        return {
            "project": _project_summary(project),
            "admitted_task_count": len(launch_requests),
            "launch_requests": launch_requests,
            "recovery": recovery,
            "warnings": warnings,
        }
