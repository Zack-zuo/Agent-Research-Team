from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional

from research_agent_team.application.errors import CommandError
from research_agent_team.storage import ProjectLayout, read_json


SINGLE_LAUNCH_KEYS = {"launch_request", "follow_up_launch_request", "next_launch_request"}
LIST_LAUNCH_KEYS = {"launch_requests"}
REQUIRED_LAUNCH_FIELDS = (
    "activation_id",
    "slot_id",
    "task_id",
    "bundle_path",
    "briefing_path",
    "runtime_metadata_path",
)
APPROVAL_REPLAY_COMMANDS = {"approve_checkpoint", "reject_checkpoint"}
EXPERIMENT_COMMANDS = {"run_experiment"}
FOLLOW_UP_COMMANDS = {"review_experiment"}
LOW_RISK_WALL_CLOCK_SECONDS = 14_400
LOW_RISK_TOKEN_BUDGET = 250_000


@dataclass(frozen=True)
class LaunchRequestCandidate:
    source_path: str
    launch_request: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"source_path": self.source_path, "launch_request": dict(self.launch_request)}


def collect_launch_requests(payload: Mapping[str, Any]) -> List[LaunchRequestCandidate]:
    """Collect known launch request fields from a command or callback result."""

    candidates: List[LaunchRequestCandidate] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else key
                if key in SINGLE_LAUNCH_KEYS:
                    if isinstance(child, dict):
                        candidates.append(LaunchRequestCandidate(source_path=child_path, launch_request=dict(child)))
                    continue
                if key in LIST_LAUNCH_KEYS:
                    if isinstance(child, list):
                        for index, item in enumerate(child):
                            if isinstance(item, dict):
                                candidates.append(
                                    LaunchRequestCandidate(
                                        source_path=f"{child_path}[{index}]",
                                        launch_request=dict(item),
                                    )
                                )
                    continue
                walk(child, child_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(payload, "")
    return candidates


def validate_launch_request_context(root_path: str, launch_request: Mapping[str, Any]) -> Dict[str, Any]:
    layout = ProjectLayout(Path(root_path))
    activation_id = _required_string(launch_request, "activation_id")
    slot_id = _required_string(launch_request, "slot_id")
    task_id = _required_string(launch_request, "task_id")
    _validate_resource_id(activation_id, "activation_id")
    _validate_resource_id(slot_id, "slot_id")
    _validate_resource_id(task_id, "task_id")
    raw_paths = _validate_launch_request_paths(launch_request)

    activation = _read_state_json(layout.activation_state_path(activation_id), "activation_not_found", "Activation does not exist", activation_id)
    task = _read_state_json(layout.task_state_path(task_id), "task_not_found", "Task does not exist", task_id)
    slot = _read_state_json(layout.slot_state_path(slot_id), "slot_not_found", "Slot does not exist", slot_id)

    _require_match(activation.get("activation_id"), activation_id, "activation_id")
    _require_match(activation.get("slot_id"), slot_id, "slot_id")
    _require_match(activation.get("task_id"), task_id, "task_id")
    _require_match(task.get("task_id"), task_id, "task_id")
    _require_match(task.get("current_activation_id"), activation_id, "task.current_activation_id")
    _require_match(slot.get("slot_id"), slot_id, "slot_id")
    _require_match(slot.get("current_activation_id"), activation_id, "slot.current_activation_id")

    if activation.get("status") != "starting":
        raise CommandError(
            "invalid_activation_state",
            "Launch request activation must still be starting before Codex can launch it.",
            activation_id=activation_id,
            status=activation.get("status"),
        )
    if task.get("status") != "admitted":
        raise CommandError(
            "invalid_task_state",
            "Launch request task must still be admitted before Codex can launch it.",
            task_id=task_id,
            status=task.get("status"),
        )

    expected_paths = _expected_launch_paths(slot_id, activation_id)
    resolved_paths: Dict[str, str] = {}
    sanitized_request: Dict[str, Any] = {
        "activation_id": activation_id,
        "slot_id": slot_id,
        "task_id": task_id,
    }
    for field_name in ("bundle_path", "briefing_path", "runtime_metadata_path"):
        value = raw_paths[field_name]
        _require_match(value.replace("\\", "/"), expected_paths[field_name], field_name)
        resolved_paths[field_name] = str(_resolve_project_file(layout, value))
        sanitized_request[field_name] = expected_paths[field_name]

    permissions_path = raw_paths.get("permissions_manifest_path")
    if permissions_path is not None:
        if not isinstance(permissions_path, str) or not permissions_path.strip():
            raise CommandError("invalid_launch_request", "permissions_manifest_path must be a non-empty string when provided")
        _require_match(permissions_path.replace("\\", "/"), expected_paths["permissions_manifest_path"], "permissions_manifest_path")
        resolved_paths["permissions_manifest_path"] = str(_resolve_project_file(layout, permissions_path))
        sanitized_request["permissions_manifest_path"] = expected_paths["permissions_manifest_path"]

    bundle = read_json(Path(resolved_paths["bundle_path"]))
    if bundle.get("task_id") != task_id or bundle.get("slot_id") != slot_id:
        raise CommandError(
            "invalid_launch_request",
            "Launch request bundle does not match activation task or slot.",
            activation_id=activation_id,
            task_id=task_id,
            slot_id=slot_id,
        )

    return {
        "launch_request": sanitized_request,
        "activation": _activation_summary(activation),
        "task": _task_summary(task),
        "slot": _slot_summary(slot),
        "bundle": _bundle_summary(bundle),
        "paths": {
            "activation_state_path": f"state/activations/{activation_id}.json",
            "task_state_path": f"state/tasks/{task_id}.json",
            "slot_state_path": f"state/slots/{slot_id}.json",
            **expected_paths,
        },
        "_resolved_paths": resolved_paths,
    }


def plan_launches(
    root_path: str,
    command_result: Mapping[str, Any],
    source_command: Optional[str] = None,
    policy: str = "conservative",
) -> Dict[str, Any]:
    if policy not in {"conservative", "confirm_all"}:
        raise CommandError("invalid_payload", f"Unsupported launch policy: {policy}", policy=policy)

    candidates = collect_launch_requests(command_result)
    effective_source_command = source_command or _infer_source_command(command_result)
    decisions: List[Dict[str, Any]] = []
    messages: List[str] = []

    if not command_result.get("ok", True):
        message = "Command did not succeed; launch handling was skipped."
        messages.append(message)
        for candidate in candidates:
            decisions.append(
                {
                    "source_path": candidate.source_path,
                    "action": "blocked",
                    "reason": message,
                    "launch_request": dict(candidate.launch_request),
                }
            )
        return {
            "policy": policy,
            "source_command": effective_source_command,
            "launch_request_count": len(candidates),
            "auto_launch_count": 0,
            "confirm_launch_count": 0,
            "blocked_count": len(decisions),
            "error_count": 0,
            "decisions": decisions,
            "messages": messages,
        }
    for candidate in candidates:
        try:
            context = validate_launch_request_context(root_path, candidate.launch_request)
            action, reason = _classify_launch(context, source_command=effective_source_command, source_path=candidate.source_path, policy=policy)
            decisions.append(
                {
                    "source_path": candidate.source_path,
                    "action": action,
                    "reason": reason,
                    **_public_context(context),
                }
            )
        except CommandError as exc:
            decisions.append(
                {
                    "source_path": candidate.source_path,
                    "action": "error",
                    "reason": exc.message,
                    "error": exc.to_dict(),
                    "launch_request": dict(candidate.launch_request),
                    **_best_effort_state(root_path, candidate.launch_request),
                }
            )

    if not candidates and not messages:
        messages.append(_no_launch_message(command_result))

    return {
        "policy": policy,
        "source_command": effective_source_command,
        "launch_request_count": len(candidates),
        "auto_launch_count": _count_actions(decisions, "auto_launch"),
        "confirm_launch_count": _count_actions(decisions, "confirm_launch"),
        "blocked_count": _count_actions(decisions, "blocked"),
        "error_count": _count_actions(decisions, "error"),
        "decisions": decisions,
        "messages": messages,
    }


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CommandError("invalid_launch_request", f"{key} is required", field=key)
    return value.strip()


def _read_state_json(path: Path, code: str, message: str, resource_id: str) -> Dict[str, Any]:
    if not path.exists():
        raise CommandError(code, f"{message}: {resource_id}", resource_id=resource_id)
    value = read_json(path)
    if not isinstance(value, dict):
        raise CommandError("invalid_state", f"State file is not an object: {path}")
    return value


def _validate_resource_id(value: str, field: str) -> None:
    if "/" in value or "\\" in value or value in {".", ".."}:
        raise CommandError("invalid_launch_request", f"{field} is invalid", field=field, value=value)


def _require_match(actual: Any, expected: str, field: str) -> None:
    if actual != expected:
        raise CommandError(
            "invalid_launch_request",
            f"Launch request field mismatch for {field}.",
            field=field,
            expected=expected,
            actual=actual,
        )


def _validate_project_relative_path(path_value: str) -> PurePosixPath:
    path = PurePosixPath(path_value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or "." in path.parts or ".." in path.parts:
        raise CommandError("invalid_launch_request", f"Launch request path escapes project root: {path_value}", path=path_value)
    return path


def _resolve_project_file(layout: ProjectLayout, project_relative_path: str) -> Path:
    relative_path = _validate_project_relative_path(project_relative_path)
    candidate = (layout.root / Path(*relative_path.parts)).resolve()
    if candidate != layout.root and layout.root not in candidate.parents:
        raise CommandError("invalid_launch_request", f"Launch request path escapes project root: {project_relative_path}", path=project_relative_path)
    if not candidate.exists():
        raise CommandError("launch_input_not_found", f"Launch input does not exist: {project_relative_path}", path=project_relative_path)
    return candidate


def _validate_launch_request_paths(launch_request: Mapping[str, Any]) -> Dict[str, str]:
    paths: Dict[str, str] = {}
    for field_name in ("bundle_path", "briefing_path", "runtime_metadata_path"):
        path_value = _required_string(launch_request, field_name)
        _validate_project_relative_path(path_value)
        paths[field_name] = path_value
    permissions_path = launch_request.get("permissions_manifest_path")
    if permissions_path is not None:
        if not isinstance(permissions_path, str) or not permissions_path.strip():
            raise CommandError("invalid_launch_request", "permissions_manifest_path must be a non-empty string when provided")
        _validate_project_relative_path(permissions_path)
        paths["permissions_manifest_path"] = permissions_path.strip()
    return paths


def _expected_launch_paths(slot_id: str, activation_id: str) -> Dict[str, str]:
    base = PurePosixPath("agents") / slot_id / "activations" / activation_id
    return {
        "bundle_path": str(base / "bundle.json"),
        "briefing_path": str(base / "briefing.md"),
        "runtime_metadata_path": str(base / "runtime.json"),
        "permissions_manifest_path": str(base / "permissions.json"),
    }


def _activation_summary(activation: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "activation_id": activation.get("activation_id"),
        "slot_id": activation.get("slot_id"),
        "task_id": activation.get("task_id"),
        "status": activation.get("status"),
        "runtime_pid": activation.get("runtime_pid"),
        "lease_timeout_seconds": activation.get("lease_timeout_seconds"),
    }


def _task_summary(task: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "task_id": task.get("task_id"),
        "owner_slot_id": task.get("owner_slot_id"),
        "status": task.get("status"),
        "title": task.get("title"),
        "review_requirement": task.get("review_requirement"),
        "success_criteria": list(task.get("success_criteria") or []),
        "expected_output_types": list(task.get("expected_output_types") or []),
        "input_path_roots": list(task.get("input_path_roots") or []),
        "budget_envelope": dict(task.get("budget_envelope") or {}),
        "current_activation_id": task.get("current_activation_id"),
    }


def _slot_summary(slot: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "slot_id": slot.get("slot_id"),
        "role": slot.get("role"),
        "status": slot.get("status"),
        "current_activation_id": slot.get("current_activation_id"),
        "queued_task_count": len(slot.get("queued_task_ids") or []),
        "active_task_count": len(slot.get("active_task_ids") or []),
    }


def _bundle_summary(bundle: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "bundle_id": bundle.get("bundle_id"),
        "task_id": bundle.get("task_id"),
        "slot_id": bundle.get("slot_id"),
        "review_gates": list(bundle.get("review_gates") or []),
        "expected_output_types": list(bundle.get("expected_output_types") or []),
        "allowed_path_roots": list(bundle.get("allowed_path_roots") or []),
        "effective_budget_envelope": dict(bundle.get("effective_budget_envelope") or {}),
        "experiment_request_id": bundle.get("experiment_request_id"),
        "experiment_run_id": bundle.get("experiment_run_id"),
        "resume_checkpoint_id": bundle.get("resume_checkpoint_id"),
    }


def _infer_source_command(command_result: Mapping[str, Any]) -> Optional[str]:
    result = command_result.get("result")
    if not isinstance(result, dict):
        return None
    if (
        isinstance(result.get("approval_id"), str)
        and isinstance(result.get("scope_type"), str)
        and "applied" in result
        and "launch_request" in result
    ):
        return "approve_checkpoint"
    return None


def _classify_launch(context: Mapping[str, Any], *, source_command: Optional[str], source_path: str, policy: str) -> tuple[str, str]:
    if policy == "confirm_all":
        return "confirm_launch", "confirm_all policy requires user confirmation before launching"

    task = context["task"]
    bundle = context["bundle"]
    if source_command in APPROVAL_REPLAY_COMMANDS:
        return "confirm_launch", "approval replay launch requires user confirmation"
    if source_command in FOLLOW_UP_COMMANDS or source_path.endswith("follow_up_launch_request"):
        return "confirm_launch", "review follow-up launch requires user confirmation"
    if source_command in EXPERIMENT_COMMANDS or task.get("review_requirement") == "experiment_review" or bundle.get("experiment_run_id"):
        return "confirm_launch", "experiment activation requires user confirmation"
    if "experiment_review_required" in bundle.get("review_gates", []):
        return "confirm_launch", "experiment activation requires user confirmation"
    if not task.get("success_criteria"):
        return "confirm_launch", "unclear task scope requires user confirmation before launching"
    if _budget_exceeds_low_risk_limit(task.get("budget_envelope") or bundle.get("effective_budget_envelope") or {}):
        return "confirm_launch", "long-running or high-budget activation requires user confirmation"
    return "auto_launch", "clear non-experiment activation is safe to launch automatically"


def _budget_exceeds_low_risk_limit(budget: Mapping[str, Any]) -> bool:
    wall_clock = _number_or_none(budget.get("wall_clock_seconds"))
    token_budget = _number_or_none(budget.get("token_budget"))
    if wall_clock is not None and wall_clock > LOW_RISK_WALL_CLOCK_SECONDS:
        return True
    if token_budget is not None and token_budget > LOW_RISK_TOKEN_BUDGET:
        return True
    return False


def _number_or_none(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _public_context(context: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "launch_request": dict(context["launch_request"]),
        "activation": dict(context["activation"]),
        "task": dict(context["task"]),
        "slot": dict(context["slot"]),
        "bundle": dict(context["bundle"]),
        "paths": dict(context["paths"]),
    }


def _best_effort_state(root_path: str, launch_request: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        layout = ProjectLayout(Path(root_path))
        activation_id = launch_request.get("activation_id")
        task_id = launch_request.get("task_id")
        slot_id = launch_request.get("slot_id")
        payload: Dict[str, Any] = {}
        if isinstance(activation_id, str) and (layout.activation_state_path(activation_id)).exists():
            payload["activation"] = _activation_summary(read_json(layout.activation_state_path(activation_id)))
        if isinstance(task_id, str) and (layout.task_state_path(task_id)).exists():
            payload["task"] = _task_summary(read_json(layout.task_state_path(task_id)))
        if isinstance(slot_id, str) and (layout.slot_state_path(slot_id)).exists():
            payload["slot"] = _slot_summary(read_json(layout.slot_state_path(slot_id)))
        return payload
    except Exception:
        return {}


def _count_actions(decisions: Iterable[Mapping[str, Any]], action: str) -> int:
    return sum(1 for decision in decisions if decision.get("action") == action)


def _no_launch_message(command_result: Mapping[str, Any]) -> str:
    result = command_result.get("result") if isinstance(command_result.get("result"), dict) else command_result
    if not isinstance(result, dict):
        return "No launch_request was returned."
    if result.get("pending_approval"):
        return "No launch_request was returned because the command is awaiting approval."
    task = result.get("task")
    if isinstance(task, dict):
        status = task.get("status")
        if status == "blocked":
            return "No launch_request was returned because the task is blocked."
        if status == "awaiting_approval":
            return "No launch_request was returned because the task is awaiting approval."
        if status == "queued":
            return "No launch_request was returned because the task is queued."
    queue_depth = result.get("owner_queue_depth")
    admitted = result.get("admitted")
    if admitted is False and isinstance(queue_depth, int) and queue_depth > 0:
        return f"No launch_request was returned; work is queued behind a busy owner slot (queue depth {queue_depth})."
    if result.get("admitted_task_count") == 0:
        return "No launch_request was returned; no queued work was admitted."
    return "No launch_request was returned."
