from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Set

from research_agent_team.application.activation_service import _read_activation, _read_task, _write_activation, _write_task
from research_agent_team.application.project_service import _read_slot, _write_slot, emit_event, load_project
from research_agent_team.domain import ActivationStatus, TaskStatus
from research_agent_team.shared import now_utc
from research_agent_team.storage import ProjectLayout, project_lock


def _parse_utc_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _empty_summary() -> Dict[str, int]:
    return {"recovered_activation_count": 0, "requeued_task_count": 0, "blocked_task_count": 0}


def recover_stale_activations_locked(layout: ProjectLayout, target_slot_ids: Optional[Set[str]] = None) -> Dict[str, int]:
    summary = _empty_summary()
    now = now_utc()
    now_dt = _parse_utc_timestamp(now)
    project = load_project(layout)
    activations_dir = layout.state_dir / "activations"
    if not activations_dir.exists():
        return summary

    for path in sorted(activations_dir.glob("*.json")):
        activation = _read_activation(layout, path.stem)
        if activation.status not in {ActivationStatus.STARTING, ActivationStatus.RUNNING}:
            continue
        if target_slot_ids is not None and activation.slot_id not in target_slot_ids:
            continue
        heartbeat_dt = _parse_utc_timestamp(activation.lease_heartbeat_at)
        if (now_dt - heartbeat_dt).total_seconds() <= activation.lease_timeout_seconds:
            continue

        slot = _read_slot(layout, activation.slot_id)
        task = _read_task(layout, activation.task_id)
        activation.status = ActivationStatus.INTERRUPTED
        activation.ended_at = now
        activation.pending_stop_reason = "stale_heartbeat"
        _write_activation(layout, activation)

        if slot.current_activation_id == activation.activation_id:
            slot.current_activation_id = None
        slot.active_task_ids = [task_id for task_id in slot.active_task_ids if task_id != task.task_id]
        task.current_activation_id = None
        task.updated_at = now
        if task.latest_checkpoint_id:
            task.status = TaskStatus.BLOCKED
            task.block_reason = "stale_activation_resume_available"
            summary["blocked_task_count"] += 1
        else:
            task.status = TaskStatus.QUEUED
            task.block_reason = None
            if task.task_id not in slot.queued_task_ids:
                slot.queued_task_ids.append(task.task_id)
            summary["requeued_task_count"] += 1

        slot.updated_at = now
        _write_task(layout, task)
        _write_slot(layout, slot)
        emit_event(
            layout,
            project.project_id,
            "activation.interrupted",
            {"reason": "stale_heartbeat"},
            slot_id=slot.slot_id,
            task_id=task.task_id,
            activation_id=activation.activation_id,
        )
        emit_event(
            layout,
            project.project_id,
            "recovery.performed",
            {"outcome": "blocked" if task.status == TaskStatus.BLOCKED else "requeued"},
            slot_id=slot.slot_id,
            task_id=task.task_id,
            activation_id=activation.activation_id,
        )
        summary["recovered_activation_count"] += 1
    return summary


def recover_stale_activations(root_path: str) -> Dict[str, int]:
    layout = ProjectLayout(Path(root_path))
    with project_lock(layout.lock_path):
        return recover_stale_activations_locked(layout)
