from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional

from research_agent_team.application.activation_service import (
    _admit_next_task_if_possible_locked,
    cancel_activation,
    complete_activation,
    fail_activation,
    interrupt_activation,
    mark_activation_running,
)
from research_agent_team.application.errors import CommandError
from research_agent_team.application.health_service import prepare_project_for_command_locked
from research_agent_team.application.project_service import _read_slot, emit_event, load_project, load_topology
from research_agent_team.application.recovery_service import recover_stale_activations, recover_stale_activations_locked
from research_agent_team.domain import ActivationStatus, AgentActivation, TaskStatus
from research_agent_team.runtime.launch_prompt import render_launch_prompt
from research_agent_team.runtime.launch_request_orchestrator import plan_launches, validate_launch_request_context
from research_agent_team.runtime.worker_launch import (
    WORKER_ACTIVE_STATUSES,
    WORKER_TERMINAL_STATUSES,
    CodexSubagentLaunchAdapter,
    WorkerLaunchAdapter,
    WorkerLaunchSpec,
    WorkerObservation,
)
from research_agent_team.shared import now_utc
from research_agent_team.storage import ProjectLayout, append_jsonl, project_lock, read_json, write_json_atomic, write_text_atomic


def _layout_from_root(root_path: str) -> ProjectLayout:
    if not isinstance(root_path, str) or not root_path.strip():
        raise CommandError("invalid_payload", "root_path is required", field="root_path")
    return ProjectLayout(Path(root_path))


def _validate_positive_int(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CommandError("invalid_payload", f"{field} must be a positive integer", field=field)
    return value


def _parse_utc_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _activation_relative_paths(slot_id: str, activation_id: str) -> Dict[str, str]:
    base = PurePosixPath("agents") / slot_id / "activations" / activation_id
    return {
        "bundle_path": str(base / "bundle.json"),
        "briefing_path": str(base / "briefing.md"),
        "runtime_metadata_path": str(base / "runtime.json"),
        "permissions_manifest_path": str(base / "permissions.json"),
    }


def _launch_request(activation: AgentActivation) -> Dict[str, str]:
    return {
        "activation_id": activation.activation_id,
        "slot_id": activation.slot_id,
        "task_id": activation.task_id,
        **_activation_relative_paths(activation.slot_id, activation.activation_id),
    }


def _activation_dir(layout: ProjectLayout, activation: AgentActivation) -> Path:
    return layout.slot_activation_root(activation.slot_id, activation.activation_id)


def _activation_from_path(path: Path) -> AgentActivation:
    return AgentActivation.from_dict(read_json(path))


def _read_activation(layout: ProjectLayout, activation_id: str) -> AgentActivation:
    path = layout.activation_state_path(activation_id)
    if not path.exists():
        raise CommandError("activation_not_found", f"Activation does not exist: {activation_id}", activation_id=activation_id)
    return AgentActivation.from_dict(read_json(path))


def _read_task_status(layout: ProjectLayout, task_id: str) -> str:
    return str(read_json(layout.task_state_path(task_id)).get("status"))


def _task_has_approved_budget_replay(layout: ProjectLayout, task_id: str) -> bool:
    approvals_dir = layout.state_dir / "approvals"
    if not approvals_dir.exists():
        return False
    for path in approvals_dir.glob("*.json"):
        approval = read_json(path)
        if (
            approval.get("scope_type") == "task_budget_override"
            and approval.get("scope_id") == task_id
            and approval.get("status") == "approved"
        ):
            return True
    return False


def _task_is_review_follow_up(layout: ProjectLayout, task_id: str) -> bool:
    reviews_dir = layout.state_dir / "experiments" / "reviews"
    if not reviews_dir.exists():
        return False
    for path in reviews_dir.glob("*.json"):
        review = read_json(path)
        if review.get("follow_up_task_id") == task_id:
            return True
    return False


def _source_command_for_activation(layout: ProjectLayout, activation: AgentActivation) -> str:
    if _task_has_approved_budget_replay(layout, activation.task_id):
        return "approve_checkpoint"
    if _task_is_review_follow_up(layout, activation.task_id):
        return "review_experiment"
    return "execution"


def _worker_path(layout: ProjectLayout, activation: AgentActivation) -> Path:
    return _activation_dir(layout, activation) / "worker.json"


def _worker_events_path(layout: ProjectLayout, activation: AgentActivation) -> Path:
    return _activation_dir(layout, activation) / "worker-events.jsonl"


def _prompt_relative_path(activation: AgentActivation) -> str:
    return str(PurePosixPath("agents") / activation.slot_id / "activations" / activation.activation_id / "launch-prompt.md")


def _read_worker_state_for_activation(layout: ProjectLayout, activation: AgentActivation) -> Optional[Dict[str, Any]]:
    path = _worker_path(layout, activation)
    if not path.exists():
        return None
    return read_json(path)


def _write_worker_state(layout: ProjectLayout, activation: AgentActivation, worker_state: Dict[str, Any]) -> None:
    worker_state["updated_at"] = now_utc()
    write_json_atomic(_worker_path(layout, activation), worker_state)


def _append_worker_event(layout: ProjectLayout, activation: AgentActivation, event_type: str, payload: Dict[str, Any]) -> None:
    append_jsonl(
        _worker_events_path(layout, activation),
        {
            "event_type": event_type,
            "created_at": now_utc(),
            "activation_id": activation.activation_id,
            "slot_id": activation.slot_id,
            "task_id": activation.task_id,
            "payload": payload,
        },
    )


def _active_worker_count(layout: ProjectLayout) -> int:
    count = 0
    for activation in _all_activations(layout):
        worker_state = _read_worker_state_for_activation(layout, activation)
        if worker_state and worker_state.get("status") in WORKER_ACTIVE_STATUSES:
            count += 1
    return count


def _all_activations(layout: ProjectLayout) -> List[AgentActivation]:
    activations_dir = layout.state_dir / "activations"
    if not activations_dir.exists():
        return []
    return [_activation_from_path(path) for path in sorted(activations_dir.glob("*.json"))]


def _pending_starting_activations(layout: ProjectLayout) -> List[AgentActivation]:
    pending: List[AgentActivation] = []
    for activation in _all_activations(layout):
        if activation.status != ActivationStatus.STARTING:
            continue
        if _read_task_status(layout, activation.task_id) != TaskStatus.ADMITTED.value:
            continue
        worker_state = _read_worker_state_for_activation(layout, activation)
        if worker_state and worker_state.get("status") in WORKER_ACTIVE_STATUSES:
            continue
        pending.append(activation)
    return pending


def _admit_queued_work_locked(layout: ProjectLayout) -> List[Dict[str, str]]:
    project = load_project(layout)
    topology = load_topology(layout)
    launch_requests: List[Dict[str, str]] = []
    for slot_id in topology.active_slot_ids:
        slot = _read_slot(layout, slot_id)
        launch_request = _admit_next_task_if_possible_locked(layout, project.status, slot)
        if launch_request is not None:
            launch_requests.append(launch_request)
    return launch_requests


def _activation_summary(activation: AgentActivation, worker_state: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    return {
        "activation_id": activation.activation_id,
        "slot_id": activation.slot_id,
        "task_id": activation.task_id,
        "status": activation.status.value,
        "runtime_pid": activation.runtime_pid,
        "lease_heartbeat_at": activation.lease_heartbeat_at,
        "lease_timeout_seconds": activation.lease_timeout_seconds,
        "checkpoint_after_id": activation.checkpoint_after_id,
        "worker": dict(worker_state or {}),
    }


def _worker_summary(worker_state: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "activation_id": worker_state.get("activation_id"),
        "slot_id": worker_state.get("slot_id"),
        "task_id": worker_state.get("task_id"),
        "status": worker_state.get("status"),
        "adapter": worker_state.get("adapter"),
        "handle": worker_state.get("handle"),
        "prompt_path": worker_state.get("prompt_path"),
        "updated_at": worker_state.get("updated_at"),
        "ended_at": worker_state.get("ended_at"),
        "diagnostics": dict(worker_state.get("diagnostics") or {}),
    }


def _project_event(layout: ProjectLayout, event_type: str, activation: AgentActivation, payload: Dict[str, Any]) -> None:
    project = load_project(layout)
    emit_event(
        layout,
        project.project_id,
        event_type,
        payload,
        slot_id=activation.slot_id,
        task_id=activation.task_id,
        activation_id=activation.activation_id,
    )


def _empty_launch_plan(policy: str) -> Dict[str, Any]:
    return {
        "policy": policy,
        "source_command": "execution",
        "launch_request_count": 0,
        "auto_launch_count": 0,
        "confirm_launch_count": 0,
        "blocked_count": 0,
        "error_count": 0,
        "decisions": [],
        "messages": ["No launch_request was returned."],
    }


def _combine_pending_launch_plan(root_path: str, layout: ProjectLayout, activations: List[AgentActivation], policy: str) -> Dict[str, Any]:
    if not activations:
        return _empty_launch_plan(policy)

    decisions: List[Dict[str, Any]] = []
    messages: List[str] = []
    for activation in activations:
        source_command = _source_command_for_activation(layout, activation)
        launch_request = _launch_request(activation)
        planned = plan_launches(
            root_path,
            {"ok": True, "result": {"launch_request": launch_request}},
            source_command=source_command,
            policy=policy,
        )
        for decision in planned["decisions"]:
            enriched = dict(decision)
            enriched["source_command"] = source_command
            decisions.append(enriched)
        messages.extend(planned.get("messages") or [])

    return {
        "policy": policy,
        "source_command": "execution",
        "launch_request_count": len(activations),
        "auto_launch_count": sum(1 for decision in decisions if decision.get("action") == "auto_launch"),
        "confirm_launch_count": sum(1 for decision in decisions if decision.get("action") == "confirm_launch"),
        "blocked_count": sum(1 for decision in decisions if decision.get("action") == "blocked"),
        "error_count": sum(1 for decision in decisions if decision.get("action") == "error"),
        "decisions": decisions,
        "messages": messages,
    }


def plan_pending_launches(root_path: str, max_concurrent: int = 1, policy: str = "conservative") -> Dict[str, Any]:
    _validate_positive_int(max_concurrent, "max_concurrent")
    layout = _layout_from_root(root_path)
    with project_lock(layout.lock_path):
        preparation = prepare_project_for_command_locked(layout)
        recovery = recover_stale_activations_locked(layout)
        active_count = _active_worker_count(layout)
        capacity_remaining = max(max_concurrent - active_count, 0)
        pending = _pending_starting_activations(layout)
        launch_requests = [_launch_request(activation) for activation in pending]
        launch_plan = _combine_pending_launch_plan(root_path, layout, pending, policy)

    return {
        "pending_count": len(pending),
        "active_worker_count": active_count,
        "capacity_remaining": capacity_remaining,
        "launch_requests": launch_requests,
        "launch_plan": launch_plan,
        "recovery": recovery,
        "warnings": preparation.warnings,
    }


def _build_launch_spec(root_path: str, activation: AgentActivation, timeout_seconds: int) -> WorkerLaunchSpec:
    launch_request = _launch_request(activation)
    context = validate_launch_request_context(root_path, launch_request)
    prompt = render_launch_prompt(root_path, launch_request)
    prompt_path = _prompt_relative_path(activation)
    return WorkerLaunchSpec(
        root_path=root_path,
        activation_id=activation.activation_id,
        slot_id=activation.slot_id,
        task_id=activation.task_id,
        prompt=prompt,
        prompt_path=prompt_path,
        timeout_seconds=timeout_seconds,
        metadata={
            "role": context["slot"].get("role"),
            "review_requirement": context["task"].get("review_requirement"),
        },
    )


def _initial_worker_state(adapter: WorkerLaunchAdapter, spec: WorkerLaunchSpec, status: str, handle: Optional[str], diagnostics: Mapping[str, Any]) -> Dict[str, Any]:
    timestamp = now_utc()
    return {
        "activation_id": spec.activation_id,
        "slot_id": spec.slot_id,
        "task_id": spec.task_id,
        "adapter": adapter.name,
        "status": status,
        "handle": handle,
        "prompt_path": spec.prompt_path,
        "timeout_seconds": spec.timeout_seconds,
        "requested_at": timestamp,
        "spawned_at": timestamp if status in {"spawned", "running", "completed", "failed", "cancelled"} else None,
        "running_at": timestamp if status == "running" else None,
        "ended_at": timestamp if status in WORKER_TERMINAL_STATUSES else None,
        "cancel_reason": None,
        "diagnostics": dict(diagnostics),
    }


def _update_worker_from_launch_result(
    layout: ProjectLayout,
    activation: AgentActivation,
    worker_state: Dict[str, Any],
    status: str,
    handle: Optional[str],
    diagnostics: Mapping[str, Any],
) -> Dict[str, Any]:
    timestamp = now_utc()
    worker_state["status"] = status
    worker_state["handle"] = handle
    if status in {"spawned", "running", "completed", "failed", "interrupted", "cancelled"}:
        worker_state["spawned_at"] = worker_state.get("spawned_at") or timestamp
    if status == "running":
        worker_state["running_at"] = worker_state.get("running_at") or timestamp
    if status in WORKER_TERMINAL_STATUSES:
        worker_state["ended_at"] = worker_state.get("ended_at") or timestamp
    merged_diagnostics = dict(worker_state.get("diagnostics") or {})
    merged_diagnostics.update(diagnostics)
    worker_state["diagnostics"] = merged_diagnostics
    _write_worker_state(layout, activation, worker_state)
    return worker_state


def start_pending_launches(
    root_path: str,
    adapter: Optional[WorkerLaunchAdapter] = None,
    max_concurrent: int = 1,
    timeout_seconds: int = 180,
    policy: str = "conservative",
) -> Dict[str, Any]:
    adapter = adapter or CodexSubagentLaunchAdapter()
    _validate_positive_int(max_concurrent, "max_concurrent")
    _validate_positive_int(timeout_seconds, "timeout_seconds")
    layout = _layout_from_root(root_path)
    with project_lock(layout.lock_path):
        preparation = prepare_project_for_command_locked(layout)
        recovery = recover_stale_activations_locked(layout)
        admitted_launch_requests = _admit_queued_work_locked(layout)
        active_count = _active_worker_count(layout)
        capacity_remaining = max(max_concurrent - active_count, 0)
        pending = _pending_starting_activations(layout)
        launch_plan = _combine_pending_launch_plan(root_path, layout, pending, policy)
        auto_launch_ids = {
            decision["launch_request"]["activation_id"]
            for decision in launch_plan["decisions"]
            if decision.get("action") == "auto_launch" and isinstance(decision.get("launch_request"), dict)
        }
        selected = [activation for activation in pending if activation.activation_id in auto_launch_ids][:capacity_remaining]
        blocked_launches = [
            {
                "activation_id": decision.get("activation", {}).get("activation_id")
                or (decision.get("launch_request") or {}).get("activation_id"),
                "action": decision.get("action"),
                "reason": decision.get("reason"),
                "source_command": decision.get("source_command"),
            }
            for decision in launch_plan["decisions"]
            if decision.get("action") != "auto_launch"
        ]
        reserved: List[tuple[AgentActivation, WorkerLaunchSpec, Dict[str, Any]]] = []
        for activation in selected:
            spec = _build_launch_spec(root_path, activation, timeout_seconds)
            write_text_atomic(layout.root / spec.prompt_path, spec.prompt)
            worker_state = _initial_worker_state(
                adapter,
                spec,
                "launch_requested",
                None,
                {"message": "Worker launch reserved by execution loop."},
            )
            _write_worker_state(layout, activation, worker_state)
            _append_worker_event(layout, activation, "worker.launch_reserved", {"adapter": adapter.name})
            _project_event(layout, "worker.launch_reserved", activation, {"adapter": adapter.name})
            reserved.append((activation, spec, worker_state))

    subagent_launch_requests: List[Dict[str, Any]] = []
    workers: List[Dict[str, Any]] = []
    for activation, spec, worker_state in reserved:
        launch_result = adapter.launch(spec)
        worker_state = _update_worker_from_launch_result(
            layout,
            activation,
            worker_state,
            launch_result.status,
            launch_result.handle,
            launch_result.diagnostics,
        )
        _append_worker_event(layout, activation, "worker.launched", {"status": launch_result.status, "handle": launch_result.handle})
        _project_event(layout, "worker.launched", activation, {"adapter": adapter.name, "status": launch_result.status, "handle": launch_result.handle})
        if launch_result.status in {"running", "completed", "failed", "interrupted", "cancelled"}:
            _apply_observation(root_path, layout, activation, WorkerObservation(launch_result.status, launch_result.diagnostics), worker_state)
            worker_state = read_json(_worker_path(layout, activation))
        subagent_launch_requests.append(spec.spawn_request(adapter.name, handle=launch_result.handle, diagnostics=launch_result.diagnostics))
        workers.append(_worker_summary(worker_state))

    return {
        "started_count": len(selected),
        "admitted_count": len(admitted_launch_requests),
        "active_worker_count": active_count,
        "capacity_remaining": max(capacity_remaining - len(selected), 0),
        "blocked_count": len(blocked_launches),
        "blocked_launches": blocked_launches,
        "launch_plan": launch_plan,
        "subagent_launch_requests": subagent_launch_requests,
        "workers": workers,
        "recovery": recovery,
        "warnings": preparation.warnings,
    }


def attach_subagent(root_path: str, activation_id: str, handle: str) -> Dict[str, Any]:
    if not isinstance(handle, str) or not handle.strip():
        raise CommandError("invalid_payload", "handle is required", field="handle")
    layout = _layout_from_root(root_path)
    with project_lock(layout.lock_path):
        activation = _read_activation(layout, activation_id)
        worker_state = _read_worker_state_for_activation(layout, activation)
        if worker_state is None:
            raise CommandError("worker_not_found", f"Worker state does not exist: {activation_id}", activation_id=activation_id)
        timestamp = now_utc()
        worker_state["handle"] = handle.strip()
        worker_state["status"] = "spawned"
        worker_state["spawned_at"] = worker_state.get("spawned_at") or timestamp
        _write_worker_state(layout, activation, worker_state)
        _append_worker_event(layout, activation, "worker.attached", {"handle": handle.strip()})
        _project_event(layout, "worker.attached", activation, {"adapter": worker_state.get("adapter"), "handle": handle.strip()})
        return _worker_summary(worker_state)


def inspect_running_activations(root_path: str) -> Dict[str, Any]:
    layout = _layout_from_root(root_path)
    with project_lock(layout.lock_path):
        workers = []
        activations = []
        for activation in _all_activations(layout):
            worker_state = _read_worker_state_for_activation(layout, activation)
            if not worker_state or worker_state.get("status") not in WORKER_ACTIVE_STATUSES:
                continue
            workers.append(_worker_summary(worker_state))
            activations.append(_activation_summary(activation, worker_state))
        return {
            "worker_count": len(workers),
            "workers": workers,
            "activations": activations,
        }


def _activation_status(layout: ProjectLayout, activation_id: str) -> ActivationStatus:
    return _read_activation(layout, activation_id).status


def _apply_observation(
    root_path: str,
    layout: ProjectLayout,
    activation: AgentActivation,
    observation: WorkerObservation,
    worker_state: Dict[str, Any],
) -> str:
    if observation.status == "unknown":
        return "unknown"

    status_before = _activation_status(layout, activation.activation_id)
    if observation.status == "running":
        if status_before == ActivationStatus.STARTING:
            mark_activation_running(root_path, activation.activation_id, None)
        worker_state["status"] = "running"
        worker_state["running_at"] = worker_state.get("running_at") or now_utc()
    elif observation.status == "completed":
        if status_before == ActivationStatus.STARTING:
            mark_activation_running(root_path, activation.activation_id, None)
            status_before = ActivationStatus.RUNNING
        if status_before in {ActivationStatus.STARTING, ActivationStatus.RUNNING}:
            complete_activation(root_path, activation.activation_id, {"output_artifact_ids": []})
        worker_state["status"] = "completed"
        worker_state["ended_at"] = worker_state.get("ended_at") or now_utc()
    elif observation.status == "failed":
        if status_before == ActivationStatus.STARTING:
            mark_activation_running(root_path, activation.activation_id, None)
            status_before = ActivationStatus.RUNNING
        if status_before in {ActivationStatus.STARTING, ActivationStatus.RUNNING}:
            fail_activation(
                root_path,
                activation.activation_id,
                {"failure_summary": observation.diagnostics.get("failure_summary") or "Worker failed."},
            )
        worker_state["status"] = "failed"
        worker_state["ended_at"] = worker_state.get("ended_at") or now_utc()
    elif observation.status == "cancelled":
        if status_before in {ActivationStatus.STARTING, ActivationStatus.RUNNING}:
            cancel_activation(root_path, activation.activation_id, {"reason": observation.diagnostics.get("reason") or "worker_cancelled"})
        worker_state["status"] = "cancelled"
        worker_state["ended_at"] = worker_state.get("ended_at") or now_utc()
    elif observation.status == "stale":
        worker_state["status"] = "stale"
        worker_state["ended_at"] = worker_state.get("ended_at") or now_utc()
    elif observation.status == "interrupted":
        if status_before in {ActivationStatus.STARTING, ActivationStatus.RUNNING}:
            interrupt_activation(root_path, activation.activation_id, {"reason": observation.diagnostics.get("reason") or "worker_interrupted"})
        worker_state["status"] = "interrupted"
        worker_state["ended_at"] = worker_state.get("ended_at") or now_utc()
    elif observation.status == "cancel_requested":
        worker_state["status"] = "cancel_requested"

    diagnostics = dict(worker_state.get("diagnostics") or {})
    diagnostics.update(observation.diagnostics)
    worker_state["diagnostics"] = diagnostics
    _write_worker_state(layout, activation, worker_state)
    _append_worker_event(layout, activation, "worker.observed", {"status": observation.status, "diagnostics": observation.diagnostics})
    return observation.status


def cancel_running_activation(
    root_path: str,
    activation_id: str,
    reason: str,
    adapter: Optional[WorkerLaunchAdapter] = None,
) -> Dict[str, Any]:
    if not isinstance(reason, str) or not reason.strip():
        raise CommandError("invalid_payload", "reason is required", field="reason")
    adapter = adapter or CodexSubagentLaunchAdapter()
    layout = _layout_from_root(root_path)
    with project_lock(layout.lock_path):
        activation = _read_activation(layout, activation_id)
        worker_state = _read_worker_state_for_activation(layout, activation)
        if worker_state is None:
            raise CommandError("worker_not_found", f"Worker state does not exist: {activation_id}", activation_id=activation_id)
        worker_state["status"] = "cancel_requested"
        worker_state["cancel_reason"] = reason.strip()
        _write_worker_state(layout, activation, worker_state)
        _append_worker_event(layout, activation, "worker.cancel_requested", {"reason": reason.strip()})

    observation = adapter.cancel(worker_state, reason.strip())
    _apply_observation(root_path, layout, activation, observation, worker_state)
    worker_state = read_json(_worker_path(layout, activation))
    _project_event(layout, "worker.cancelled" if worker_state.get("status") == "cancelled" else "worker.cancel_requested", activation, {"reason": reason.strip()})
    return _worker_summary(worker_state)


def _mark_worker_from_terminal_activation(layout: ProjectLayout, activation: AgentActivation, worker_state: Dict[str, Any]) -> Optional[str]:
    if activation.status == ActivationStatus.INTERRUPTED and activation.pending_stop_reason == "stale_heartbeat":
        worker_state["status"] = "stale"
        worker_state["ended_at"] = worker_state.get("ended_at") or activation.ended_at or now_utc()
        diagnostics = dict(worker_state.get("diagnostics") or {})
        diagnostics["reason"] = "stale_heartbeat"
        worker_state["diagnostics"] = diagnostics
        _write_worker_state(layout, activation, worker_state)
        return "stale"
    if activation.status == ActivationStatus.INTERRUPTED:
        worker_state["status"] = "interrupted"
        worker_state["ended_at"] = worker_state.get("ended_at") or activation.ended_at or now_utc()
        diagnostics = dict(worker_state.get("diagnostics") or {})
        diagnostics["reason"] = activation.pending_stop_reason or "interrupted"
        worker_state["diagnostics"] = diagnostics
        _write_worker_state(layout, activation, worker_state)
        return "interrupted"
    if activation.status in {ActivationStatus.COMPLETED, ActivationStatus.FAILED, ActivationStatus.CANCELLED}:
        worker_state["status"] = activation.status.value
        worker_state["ended_at"] = worker_state.get("ended_at") or activation.ended_at or now_utc()
        if activation.failure_summary:
            diagnostics = dict(worker_state.get("diagnostics") or {})
            diagnostics["failure_summary"] = activation.failure_summary
            worker_state["diagnostics"] = diagnostics
        _write_worker_state(layout, activation, worker_state)
        return activation.status.value
    return None


def reconcile_activations(root_path: str, adapter: Optional[WorkerLaunchAdapter] = None) -> Dict[str, Any]:
    adapter = adapter or CodexSubagentLaunchAdapter()
    layout = _layout_from_root(root_path)
    recovery = recover_stale_activations(root_path)
    counts = {
        "observed_count": 0,
        "running_count": 0,
        "completed_count": 0,
        "failed_count": 0,
        "interrupted_count": 0,
        "cancelled_count": 0,
        "stale_count": 0,
    }
    workers: List[Dict[str, Any]] = []

    for activation in _all_activations(layout):
        worker_state = _read_worker_state_for_activation(layout, activation)
        if worker_state is None:
            continue
        activation = _read_activation(layout, activation.activation_id)
        terminal_status = _mark_worker_from_terminal_activation(layout, activation, worker_state)
        if terminal_status:
            if terminal_status == "completed":
                counts["completed_count"] += 1
            elif terminal_status == "failed":
                counts["failed_count"] += 1
            elif terminal_status == "interrupted":
                counts["interrupted_count"] += 1
            elif terminal_status == "cancelled":
                counts["cancelled_count"] += 1
            elif terminal_status == "stale":
                counts["stale_count"] += 1
            workers.append(_worker_summary(read_json(_worker_path(layout, activation))))
            continue
        if worker_state.get("status") in WORKER_TERMINAL_STATUSES:
            workers.append(_worker_summary(worker_state))
            continue

        observation = adapter.observe(worker_state)
        counts["observed_count"] += 1
        if observation.status == "running":
            counts["running_count"] += 1
        elif observation.status == "completed":
            counts["completed_count"] += 1
        elif observation.status == "failed":
            counts["failed_count"] += 1
        elif observation.status == "interrupted":
            counts["interrupted_count"] += 1
        elif observation.status == "cancelled":
            counts["cancelled_count"] += 1
        elif observation.status == "stale":
            counts["stale_count"] += 1
        _apply_observation(root_path, layout, activation, observation, worker_state)
        workers.append(_worker_summary(read_json(_worker_path(layout, activation))))

    return {
        **counts,
        "worker_count": len(workers),
        "workers": workers,
        "recovery": recovery,
    }
