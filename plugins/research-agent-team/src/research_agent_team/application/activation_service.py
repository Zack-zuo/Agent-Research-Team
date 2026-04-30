from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from research_agent_team.application.errors import CommandError
from research_agent_team.application.project_service import _read_slot, _write_slot, emit_event, load_project
from research_agent_team.domain import AgentActivation, AgentSlot, ActivationStatus, ProjectStatus, SlotCheckpoint, SlotStatus, Task, TaskBundle, TaskStatus
from research_agent_team.shared import new_id, now_utc
from research_agent_team.storage import ProjectLayout, append_jsonl, project_lock, read_json, write_json_atomic, write_text_atomic


def _layout_from_root(root_path: str) -> ProjectLayout:
    if not isinstance(root_path, str) or not root_path.strip():
        raise CommandError("invalid_payload", "root_path is required", field="root_path")
    return ProjectLayout(Path(root_path))


def _read_task(layout: ProjectLayout, task_id: str) -> Task:
    path = layout.task_state_path(task_id)
    if not path.exists():
        raise CommandError("task_not_found", f"Task does not exist: {task_id}", task_id=task_id)
    return Task.from_dict(read_json(path))


def _write_task(layout: ProjectLayout, task: Task) -> None:
    write_json_atomic(layout.task_state_path(task.task_id), task.to_dict())


def _read_activation(layout: ProjectLayout, activation_id: str) -> AgentActivation:
    path = layout.activation_state_path(activation_id)
    if not path.exists():
        raise CommandError("activation_not_found", f"Activation does not exist: {activation_id}", activation_id=activation_id)
    return AgentActivation.from_dict(read_json(path))


def _write_activation(layout: ProjectLayout, activation: AgentActivation) -> None:
    write_json_atomic(layout.activation_state_path(activation.activation_id), activation.to_dict())


def _activation_relative_paths(slot_id: str, activation_id: str) -> Dict[str, str]:
    base = PurePosixPath("agents") / slot_id / "activations" / activation_id
    return {
        "bundle_path": str(base / "bundle.json"),
        "briefing_path": str(base / "briefing.md"),
        "runtime_metadata_path": str(base / "runtime.json"),
    }


def _launch_request(activation: AgentActivation) -> Dict[str, str]:
    return {
        "activation_id": activation.activation_id,
        "slot_id": activation.slot_id,
        "task_id": activation.task_id,
        **_activation_relative_paths(activation.slot_id, activation.activation_id),
    }


def _briefing_text(task: Task) -> str:
    success_lines = [f"- {criterion}" for criterion in task.success_criteria] or ["- No success criteria provided."]
    output_lines = [f"- {output_type}" for output_type in task.expected_output_types] or ["- No declared output types."]
    path_lines = [f"- {path_root}" for path_root in task.input_path_roots] or ["- No input path roots."]
    return "\n".join(
        [
            f"# {task.title}",
            "",
            task.description,
            "",
            "## Success Criteria",
            *success_lines,
            "",
            "## Expected Outputs",
            *output_lines,
            "",
            "## Allowed Path Roots",
            *path_lines,
            "",
            "## Effective Budget Envelope",
            f"`{task.budget_envelope}`",
            "",
        ]
    )


def _granted_permissions(task: Task) -> List[str]:
    grants = ["read:bundle", "write:workspace", "checkpoint:slot", "callback:activation"]
    grants.extend(f"read:{path_root}" for path_root in task.input_path_roots)
    grants.extend(f"artifact:{artifact_id}" for artifact_id in task.input_artifact_ids)
    return grants


def _validate_output_descriptor(slot_id: str, descriptor: Dict[str, Any]) -> PurePosixPath:
    raw_path = descriptor.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise CommandError("invalid_payload", "output artifact path is required", field="output_artifacts.path")
    relative_path = PurePosixPath(raw_path.replace("\\", "/"))
    if relative_path.is_absolute() or "." in relative_path.parts or ".." in relative_path.parts or not relative_path.parts:
        raise CommandError("invalid_artifact_path", "Output artifact paths must be project-relative", path=raw_path)
    if relative_path.parts[:1] == ("shared",) or relative_path.parts[:2] == ("agents", slot_id):
        return relative_path
    raise CommandError(
        "invalid_artifact_path",
        "Output artifacts must live under shared/ or the producing slot directory",
        path=raw_path,
    )


def _publish_output_artifacts(layout: ProjectLayout, activation: AgentActivation, task: Task, descriptors: List[Dict[str, Any]]) -> List[str]:
    artifact_ids: List[str] = []
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            raise CommandError("invalid_payload", "output_artifacts entries must be objects", field="output_artifacts")
        relative_path = _validate_output_descriptor(activation.slot_id, descriptor)
        resolved = layout.project_relative_path(str(relative_path))
        if not resolved.exists():
            raise CommandError("artifact_path_not_found", f"Output artifact path does not exist: {relative_path}", path=str(relative_path))
        artifact = {
            "artifact_id": str(descriptor.get("artifact_id") or new_id("artifact")),
            "type": str(descriptor.get("type", "activation_output")),
            "path": str(relative_path),
            "visibility": str(descriptor.get("visibility", "slot_private")),
            "producing_slot_id": activation.slot_id,
            "producing_activation_id": activation.activation_id,
            "task_id": task.task_id,
            "source_artifact_ids": list(descriptor.get("source_artifact_ids") or task.input_artifact_ids),
            "created_at": now_utc(),
        }
        append_jsonl(layout.artifact_index, artifact)
        artifact_ids.append(artifact["artifact_id"])
    return artifact_ids


def _admit_next_task_if_possible_locked(layout: ProjectLayout, project_status: ProjectStatus, slot: AgentSlot) -> Optional[Dict[str, str]]:
    if project_status != ProjectStatus.ACTIVE:
        return None
    if slot.status != SlotStatus.ACTIVE:
        return None
    if slot.current_activation_id is not None:
        return None
    if not slot.queued_task_ids:
        return None

    task = _read_task(layout, slot.queued_task_ids[0])
    timestamp = now_utc()
    activation_id = new_id("activation")
    bundle_id = new_id("bundle")
    activation = AgentActivation(
        activation_id=activation_id,
        slot_id=slot.slot_id,
        task_id=task.task_id,
        bundle_id=bundle_id,
        status=ActivationStatus.STARTING,
        lease_acquired_at=timestamp,
        lease_heartbeat_at=timestamp,
        lease_timeout_seconds=180,
        input_artifact_ids=list(task.input_artifact_ids),
        output_artifact_ids=[],
        checkpoint_before_id=task.latest_checkpoint_id,
    )
    bundle = TaskBundle(
        bundle_id=bundle_id,
        task_id=task.task_id,
        slot_id=slot.slot_id,
        briefing_summary=task.title,
        success_criteria=list(task.success_criteria),
        allowed_artifact_ids=list(task.input_artifact_ids),
        allowed_path_roots=list(task.input_path_roots),
        expected_output_types=list(task.expected_output_types),
        effective_budget_envelope=dict(task.budget_envelope),
        granted_permissions=_granted_permissions(task),
        review_gates=["experiment_review_required"] if task.review_requirement == "experiment_review" else [],
        resume_checkpoint_id=task.latest_checkpoint_id,
        generated_at=timestamp,
    )

    activation_dir = layout.slot_activation_root(slot.slot_id, activation_id)
    activation_dir.mkdir(parents=True, exist_ok=True)
    _write_activation(layout, activation)
    write_json_atomic(activation_dir / "bundle.json", bundle.to_dict())
    write_text_atomic(activation_dir / "briefing.md", _briefing_text(task))
    write_json_atomic(
        activation_dir / "runtime.json",
        {
            "activation_id": activation.activation_id,
            "bundle_id": activation.bundle_id,
            "task_id": task.task_id,
            "slot_id": slot.slot_id,
            "status": activation.status.value,
            "lease_timeout_seconds": activation.lease_timeout_seconds,
            "callback_commands": ["mark-running", "heartbeat", "checkpoint", "complete", "fail", "interrupt", "cancel"],
        },
    )

    slot.current_activation_id = activation_id
    slot.queued_task_ids = slot.queued_task_ids[1:]
    if task.task_id not in slot.active_task_ids:
        slot.active_task_ids.append(task.task_id)
    slot.updated_at = timestamp
    _write_slot(layout, slot)

    task.status = TaskStatus.ADMITTED
    task.current_activation_id = activation_id
    task.updated_at = timestamp
    _write_task(layout, task)

    project = load_project(layout)
    emit_event(layout, project.project_id, "task.admitted", {"slot_id": slot.slot_id}, slot_id=slot.slot_id, task_id=task.task_id)
    emit_event(
        layout,
        project.project_id,
        "activation.created",
        {"slot_id": slot.slot_id},
        slot_id=slot.slot_id,
        task_id=task.task_id,
        activation_id=activation_id,
    )
    return _launch_request(activation)


def admit_next_task_if_possible(root_path: str, slot_id: str) -> Optional[Dict[str, str]]:
    layout = _layout_from_root(root_path)
    with project_lock(layout.lock_path):
        project = load_project(layout)
        slot = _read_slot(layout, slot_id)
        return _admit_next_task_if_possible_locked(layout, project.status, slot)


def mark_activation_running(root_path: str, activation_id: str, runtime_pid: Optional[int]) -> Dict[str, Any]:
    layout = _layout_from_root(root_path)
    with project_lock(layout.lock_path):
        activation = _read_activation(layout, activation_id)
        if activation.status != ActivationStatus.STARTING:
            raise CommandError("invalid_activation_state", "Only starting activations can be marked running", activation_id=activation_id)
        task = _read_task(layout, activation.task_id)
        project = load_project(layout)
        timestamp = now_utc()

        activation.status = ActivationStatus.RUNNING
        activation.started_at = timestamp
        activation.lease_heartbeat_at = timestamp
        activation.runtime_pid = runtime_pid
        _write_activation(layout, activation)

        task.status = TaskStatus.RUNNING
        task.started_at = task.started_at or timestamp
        task.updated_at = timestamp
        _write_task(layout, task)

        emit_event(
            layout,
            project.project_id,
            "activation.started",
            {"runtime_pid": runtime_pid},
            slot_id=activation.slot_id,
            task_id=task.task_id,
            activation_id=activation.activation_id,
        )
        return {"activation_id": activation.activation_id, "status": activation.status.value}


def heartbeat_activation(root_path: str, activation_id: str, budget_delta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    layout = _layout_from_root(root_path)
    with project_lock(layout.lock_path):
        activation = _read_activation(layout, activation_id)
        if activation.status != ActivationStatus.RUNNING:
            return {
                "hard_stop_required": False,
                "triggered_thresholds": [],
                "pending_stop_reason": activation.pending_stop_reason,
            }

        task = _read_task(layout, activation.task_id)
        project = load_project(layout)
        timestamp = now_utc()
        activation.lease_heartbeat_at = timestamp
        hard_stop_required = False
        triggered_thresholds: List[str] = []

        if budget_delta:
            budget = read_json(layout.budget_state)
            consumed = dict(budget.get("consumed_to_date") or {})
            consumed.setdefault("project", {})
            consumed.setdefault("slot", {})
            consumed.setdefault("task", {})
            consumed.setdefault("activation", {})
            consumed["slot"].setdefault(activation.slot_id, {})
            consumed["task"].setdefault(task.task_id, {})
            consumed["activation"].setdefault(activation.activation_id, {})
            thresholds = budget.get("thresholds") or {}
            soft_fraction = float(thresholds.get("soft_fraction", thresholds.get("soft", 0.8)))
            hard_fraction = float(thresholds.get("hard_fraction", thresholds.get("hard", 1.0)))

            for dimension, raw_delta in budget_delta.items():
                if isinstance(raw_delta, bool) or not isinstance(raw_delta, (int, float)) or raw_delta <= 0:
                    continue
                delta = float(raw_delta)
                activation_previous = float(activation.consumed_budget.get(dimension, 0.0))
                activation_current = activation_previous + delta
                activation.consumed_budget[dimension] = activation_current
                task_bucket = consumed["task"][task.task_id]
                task_previous = float(task_bucket.get(dimension, 0.0))
                task_current = task_previous + delta
                consumed["project"][dimension] = float(consumed["project"].get(dimension, 0.0)) + delta
                consumed["slot"][activation.slot_id][dimension] = float(consumed["slot"][activation.slot_id].get(dimension, 0.0)) + delta
                task_bucket[dimension] = task_current
                consumed["activation"][activation.activation_id][dimension] = (
                    float(consumed["activation"][activation.activation_id].get(dimension, 0.0)) + delta
                )

                limit = task.budget_envelope.get(dimension)
                if isinstance(limit, (int, float)) and float(limit) > 0:
                    previous_ratio = task_previous / float(limit)
                    current_ratio = task_current / float(limit)
                    if previous_ratio < soft_fraction <= current_ratio:
                        triggered_thresholds.append(dimension)
                        emit_event(
                            layout,
                            project.project_id,
                            "budget.threshold_hit",
                            {"dimension": dimension, "threshold": "soft", "consumed": task_current, "limit": float(limit)},
                            slot_id=activation.slot_id,
                            task_id=task.task_id,
                            activation_id=activation.activation_id,
                        )
                    if current_ratio >= hard_fraction:
                        hard_stop_required = True
                    if previous_ratio < hard_fraction <= current_ratio:
                        emit_event(
                            layout,
                            project.project_id,
                            "budget.threshold_hit",
                            {"dimension": dimension, "threshold": "hard", "consumed": task_current, "limit": float(limit)},
                            slot_id=activation.slot_id,
                            task_id=task.task_id,
                            activation_id=activation.activation_id,
                        )

            budget["consumed_to_date"] = consumed
            budget["last_recomputed_at"] = timestamp
            write_json_atomic(layout.budget_state, budget)
            if hard_stop_required:
                activation.pending_stop_reason = "budget_hard_limit"

        _write_activation(layout, activation)
        return {
            "hard_stop_required": hard_stop_required,
            "triggered_thresholds": triggered_thresholds,
            "pending_stop_reason": activation.pending_stop_reason,
        }


def persist_checkpoint(root_path: str, activation_id: str, checkpoint_payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_root(root_path)
    with project_lock(layout.lock_path):
        activation = _read_activation(layout, activation_id)
        if activation.status not in {ActivationStatus.STARTING, ActivationStatus.RUNNING}:
            raise CommandError("invalid_activation_state", "Only active activations can checkpoint", activation_id=activation_id)
        task = _read_task(layout, activation.task_id)
        slot = _read_slot(layout, activation.slot_id)
        project = load_project(layout)
        summary = checkpoint_payload.get("summary")
        resume_instructions = checkpoint_payload.get("resume_instructions")
        if not isinstance(summary, str) or not summary.strip():
            raise CommandError("invalid_payload", "checkpoint summary is required", field="summary")
        if not isinstance(resume_instructions, str) or not resume_instructions.strip():
            raise CommandError("invalid_payload", "checkpoint resume_instructions is required", field="resume_instructions")

        timestamp = now_utc()
        checkpoint_id = new_id("checkpoint")
        checkpoint_dir = layout.slot_checkpoint_root(slot.slot_id, checkpoint_id)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        output_artifact_ids = list(checkpoint_payload.get("output_artifact_ids") or [])
        output_artifact_ids.extend(_publish_output_artifacts(layout, activation, task, list(checkpoint_payload.get("output_artifacts") or [])))
        checkpoint = SlotCheckpoint(
            checkpoint_id=checkpoint_id,
            slot_id=slot.slot_id,
            task_id=task.task_id,
            activation_id=activation.activation_id,
            created_at=timestamp,
            summary=summary,
            resume_instructions=resume_instructions,
            output_artifact_ids=output_artifact_ids,
            materialized_path=str(PurePosixPath("agents") / slot.slot_id / "checkpoints" / checkpoint_id),
        )
        write_text_atomic(checkpoint_dir / "summary.md", summary if summary.endswith("\n") else summary + "\n")
        write_json_atomic(
            checkpoint_dir / "context.json",
            {
                "checkpoint_id": checkpoint_id,
                "task_id": task.task_id,
                "activation_id": activation.activation_id,
                "resume_instructions": resume_instructions,
                "output_artifact_ids": output_artifact_ids,
            },
        )
        write_json_atomic(layout.slot_checkpoint_state_path(slot.slot_id), checkpoint.to_dict())

        slot.latest_checkpoint_id = checkpoint_id
        slot.updated_at = timestamp
        _write_slot(layout, slot)
        task.latest_checkpoint_id = checkpoint_id
        task.updated_at = timestamp
        _write_task(layout, task)
        activation.checkpoint_after_id = checkpoint_id
        activation.output_artifact_ids = output_artifact_ids
        _write_activation(layout, activation)

        emit_event(
            layout,
            project.project_id,
            "checkpoint.persisted",
            {"checkpoint_id": checkpoint_id},
            slot_id=slot.slot_id,
            task_id=task.task_id,
            activation_id=activation.activation_id,
        )
        return {
            "checkpoint_id": checkpoint_id,
            "published_artifact_ids": output_artifact_ids,
            "hard_stop_required": bool(activation.pending_stop_reason),
        }


def _finish_activation(
    layout: ProjectLayout,
    activation: AgentActivation,
    task: Task,
    slot: AgentSlot,
    terminal_status: ActivationStatus,
    task_status: TaskStatus,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    project = load_project(layout)
    timestamp = now_utc()
    output_artifact_ids = list(payload.get("output_artifact_ids") or [])
    output_artifact_ids.extend(_publish_output_artifacts(layout, activation, task, list(payload.get("output_artifacts") or [])))

    activation.status = terminal_status
    activation.ended_at = timestamp
    activation.output_artifact_ids = output_artifact_ids
    if terminal_status == ActivationStatus.FAILED:
        activation.failure_summary = payload.get("failure_summary")
    if terminal_status in {ActivationStatus.INTERRUPTED, ActivationStatus.CANCELLED}:
        activation.pending_stop_reason = payload.get("reason") or terminal_status.value
    _write_activation(layout, activation)

    if slot.current_activation_id == activation.activation_id:
        slot.current_activation_id = None
    slot.active_task_ids = [task_id for task_id in slot.active_task_ids if task_id != task.task_id]

    task.current_activation_id = None
    task.started_at = task.started_at or timestamp
    task.updated_at = timestamp
    task.status = task_status
    if task_status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        task.completed_at = timestamp
        if task.task_id not in slot.completed_task_ids:
            slot.completed_task_ids.append(task.task_id)
    if task_status == TaskStatus.BLOCKED:
        task.block_reason = payload.get("block_reason") or "resume_available_after_failure"
    elif task_status == TaskStatus.QUEUED:
        task.block_reason = None
        if task.task_id not in slot.queued_task_ids:
            slot.queued_task_ids.insert(0, task.task_id)
    else:
        task.block_reason = None

    slot.updated_at = timestamp
    _write_slot(layout, slot)
    _write_task(layout, task)

    event_suffix = terminal_status.value
    emit_event(
        layout,
        project.project_id,
        f"activation.{event_suffix}",
        {"failure_summary": activation.failure_summary, "reason": activation.pending_stop_reason},
        slot_id=slot.slot_id,
        task_id=task.task_id,
        activation_id=activation.activation_id,
    )
    emit_event(
        layout,
        project.project_id,
        f"task.{task.status.value}",
        {"block_reason": task.block_reason},
        slot_id=slot.slot_id,
        task_id=task.task_id,
        activation_id=activation.activation_id,
    )
    next_launch_request = None
    if terminal_status in {ActivationStatus.COMPLETED, ActivationStatus.FAILED, ActivationStatus.CANCELLED}:
        next_launch_request = _admit_next_task_if_possible_locked(layout, project.status, slot)
    return {
        "published_artifact_ids": output_artifact_ids,
        "next_launch_request": next_launch_request,
    }


def complete_activation(root_path: str, activation_id: str, completion_payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_root(root_path)
    with project_lock(layout.lock_path):
        activation = _read_activation(layout, activation_id)
        if activation.status not in {ActivationStatus.STARTING, ActivationStatus.RUNNING}:
            raise CommandError("invalid_activation_state", "Only active activations can complete", activation_id=activation_id)
        task = _read_task(layout, activation.task_id)
        slot = _read_slot(layout, activation.slot_id)
        return _finish_activation(layout, activation, task, slot, ActivationStatus.COMPLETED, TaskStatus.COMPLETED, completion_payload)


def fail_activation(root_path: str, activation_id: str, failure_payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_root(root_path)
    with project_lock(layout.lock_path):
        activation = _read_activation(layout, activation_id)
        if activation.status not in {ActivationStatus.STARTING, ActivationStatus.RUNNING}:
            raise CommandError("invalid_activation_state", "Only active activations can fail", activation_id=activation_id)
        task = _read_task(layout, activation.task_id)
        slot = _read_slot(layout, activation.slot_id)
        task_status = TaskStatus.BLOCKED if task.latest_checkpoint_id else TaskStatus.FAILED
        return _finish_activation(layout, activation, task, slot, ActivationStatus.FAILED, task_status, failure_payload)


def interrupt_activation(root_path: str, activation_id: str, interrupt_payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_root(root_path)
    with project_lock(layout.lock_path):
        activation = _read_activation(layout, activation_id)
        if activation.status not in {ActivationStatus.STARTING, ActivationStatus.RUNNING}:
            raise CommandError("invalid_activation_state", "Only active activations can be interrupted", activation_id=activation_id)
        task = _read_task(layout, activation.task_id)
        slot = _read_slot(layout, activation.slot_id)
        task_status = TaskStatus.BLOCKED if task.latest_checkpoint_id else TaskStatus.QUEUED
        if task_status == TaskStatus.BLOCKED:
            interrupt_payload = {**interrupt_payload, "block_reason": "interrupted_resume_available"}
        return _finish_activation(layout, activation, task, slot, ActivationStatus.INTERRUPTED, task_status, interrupt_payload)


def cancel_activation(root_path: str, activation_id: str, cancel_payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_root(root_path)
    with project_lock(layout.lock_path):
        activation = _read_activation(layout, activation_id)
        if activation.status not in {ActivationStatus.STARTING, ActivationStatus.RUNNING}:
            raise CommandError("invalid_activation_state", "Only active activations can be cancelled", activation_id=activation_id)
        task = _read_task(layout, activation.task_id)
        slot = _read_slot(layout, activation.slot_id)
        return _finish_activation(layout, activation, task, slot, ActivationStatus.CANCELLED, TaskStatus.CANCELLED, cancel_payload)
