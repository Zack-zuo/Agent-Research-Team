from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

from research_agent_team.application.adapter_registry import adapter_health_warnings, normalize_adapter_health
from research_agent_team.application.errors import CommandError
from research_agent_team.application.migration_service import MigrationPreparation, prepare_project_schema_locked
from research_agent_team.config import default_adapter_health, default_hook_config
from research_agent_team.domain import ActivationStatus
from research_agent_team.shared import utc_date, now_utc
from research_agent_team.storage import ProjectLayout, append_jsonl, ensure_support_surfaces, read_json, write_json_atomic


def _ensure_file(path: Path, warnings: List[str], layout: ProjectLayout) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    warnings.append(f"recreated support file: {path.relative_to(layout.root)}")


def _copy_latest_alias(layout: ProjectLayout, alias: str, directory: Path, pattern: str, warnings: List[str]) -> None:
    alias_path = layout.root / alias
    if alias_path.exists():
        return
    candidates = [path for path in sorted(directory.glob(pattern)) if path.name != alias_path.name and path.is_file()]
    if not candidates:
        return
    source = candidates[-1]
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, alias_path)
    warnings.append(f"recreated latest alias: {alias}")


def _repair_support_surfaces(layout: ProjectLayout, slot_ids: Iterable[str]) -> List[str]:
    warnings = ensure_support_surfaces(layout, slot_ids)
    if not layout.hook_config.exists():
        write_json_atomic(layout.hook_config, default_hook_config())
        warnings.append("recreated support file: state/hooks/config.json")
    if not layout.adapter_health.exists():
        write_json_atomic(layout.adapter_health, default_adapter_health())
        warnings.append("recreated support file: state/adapters/health.json")

    today = utc_date(now_utc())
    for path in [
        layout.events_dir / f"{today}.jsonl",
        layout.messages_dir / f"{today}.jsonl",
        layout.hook_logs_dir / f"{today}.jsonl",
        layout.artifact_index,
    ]:
        _ensure_file(path, warnings, layout)

    _copy_latest_alias(layout, "shared/reports/status-latest.md", layout.root / "shared" / "reports", "status-*.md", warnings)
    _copy_latest_alias(layout, "shared/reports/topology-latest.md", layout.root / "shared" / "reports", "topology-*.md", warnings)
    _copy_latest_alias(
        layout,
        "shared/reports/pending-approvals-latest.md",
        layout.root / "shared" / "reports",
        "pending-approvals-*.md",
        warnings,
    )
    _copy_latest_alias(
        layout,
        "shared/reports/final-package-latest.md",
        layout.root / "shared" / "reports",
        "final-package-*.md",
        warnings,
    )
    _copy_latest_alias(
        layout,
        "shared/graph/graph-export-latest.json",
        layout.root / "shared" / "graph",
        "graph-export-*.json",
        warnings,
    )
    _copy_latest_alias(
        layout,
        "shared/graph/graph-report-latest.md",
        layout.root / "shared" / "graph",
        "graph-report-*.md",
        warnings,
    )
    return warnings


def _json_records(directory: Path) -> List[Dict[str, Any]]:
    if not directory.exists():
        return []
    records: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        records.append(read_json(path))
    return records


def _artifact_records(layout: ProjectLayout) -> List[Dict[str, Any]]:
    if not layout.artifact_index.exists():
        return []
    records: List[Dict[str, Any]] = []
    for line in layout.artifact_index.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def _validate_project_relative(layout: ProjectLayout, relative_path: str, label: str) -> None:
    try:
        layout.project_relative_path(relative_path)
    except ValueError as exc:
        raise ValueError(f"{label} path escapes project root: {relative_path}") from exc


def _validate_artifacts(layout: ProjectLayout) -> None:
    for artifact in _artifact_records(layout):
        artifact_id = artifact.get("artifact_id", "unknown-artifact")
        path = artifact.get("path")
        if isinstance(path, str):
            _validate_project_relative(layout, path, f"Artifact {artifact_id}")


def _validate_activations(layout: ProjectLayout, slot_ids: Set[str], task_ids: Set[str]) -> Set[str]:
    activation_ids: Set[str] = set()
    active_by_slot: Dict[str, List[str]] = {}
    for activation in _json_records(layout.state_dir / "activations"):
        activation_id = str(activation.get("activation_id"))
        activation_ids.add(activation_id)
        slot_id = activation.get("slot_id")
        task_id = activation.get("task_id")
        if slot_id not in slot_ids:
            raise ValueError(f"Activation {activation_id} references missing slot {slot_id}")
        if task_id not in task_ids:
            raise ValueError(f"Activation {activation_id} references missing task {task_id}")
        if activation.get("status") in {ActivationStatus.STARTING.value, ActivationStatus.RUNNING.value}:
            active_by_slot.setdefault(str(slot_id), []).append(activation_id)
    for slot_id, activation_ids_for_slot in active_by_slot.items():
        if len(activation_ids_for_slot) > 1:
            raise ValueError(f"Slot {slot_id} has multiple active activations: {', '.join(sorted(activation_ids_for_slot))}")
    return activation_ids


def _validate_slots(layout: ProjectLayout, activation_ids: Set[str]) -> Set[str]:
    slot_ids: Set[str] = set()
    for slot in _json_records(layout.state_dir / "slots"):
        slot_id = str(slot.get("slot_id"))
        slot_ids.add(slot_id)
        current_activation_id = slot.get("current_activation_id")
        if current_activation_id and current_activation_id not in activation_ids:
            raise ValueError(f"Slot {slot_id} references missing current activation {current_activation_id}")
    return slot_ids


def _validate_tasks(layout: ProjectLayout, slot_ids: Set[str], activation_ids: Set[str]) -> Set[str]:
    task_ids: Set[str] = set()
    for task in _json_records(layout.state_dir / "tasks"):
        task_id = str(task.get("task_id"))
        task_ids.add(task_id)
        owner_slot_id = task.get("owner_slot_id")
        if owner_slot_id not in slot_ids:
            raise ValueError(f"Task {task_id} references missing owner slot {owner_slot_id}")
        current_activation_id = task.get("current_activation_id")
        if current_activation_id and current_activation_id not in activation_ids:
            raise ValueError(f"Task {task_id} references missing activation {current_activation_id}")
    return task_ids


def _validate_experiments(layout: ProjectLayout, slot_ids: Set[str], task_ids: Set[str], activation_ids: Set[str]) -> None:
    requests = _json_records(layout.state_dir / "experiments" / "requests")
    runs = _json_records(layout.state_dir / "experiments" / "runs")
    comparisons = _json_records(layout.state_dir / "experiments" / "comparisons")
    reviews = _json_records(layout.state_dir / "experiments" / "reviews")
    request_ids = {str(request.get("experiment_request_id")) for request in requests}
    run_ids = {str(run.get("experiment_run_id")) for run in runs}
    comparison_ids = {str(comparison.get("comparison_id")) for comparison in comparisons}

    for request in requests:
        request_id = request.get("experiment_request_id")
        if request.get("task_id") not in task_ids:
            raise ValueError(f"Experiment request {request_id} references missing task {request.get('task_id')}")
        for slot_key in ["requester_slot_id", "executor_slot_id", "reviewer_slot_id"]:
            if request.get(slot_key) not in slot_ids:
                raise ValueError(f"Experiment request {request_id} references missing slot {request.get(slot_key)}")

    for run in runs:
        run_id = run.get("experiment_run_id")
        if run.get("experiment_request_id") not in request_ids:
            raise ValueError(f"Experiment run {run_id} references missing request {run.get('experiment_request_id')}")
        if run.get("task_id") not in task_ids:
            raise ValueError(f"Experiment run {run_id} references missing task {run.get('task_id')}")
        activation_id = run.get("activation_id")
        if activation_id and activation_id not in activation_ids:
            raise ValueError(f"Experiment run {run_id} references missing activation {activation_id}")

    for comparison in comparisons:
        comparison_id = comparison.get("comparison_id")
        if comparison.get("primary_run_id") not in run_ids:
            raise ValueError(f"Experiment comparison {comparison_id} references missing run {comparison.get('primary_run_id')}")
        for run_id in comparison.get("compared_run_ids", []):
            if run_id not in run_ids:
                raise ValueError(f"Experiment comparison {comparison_id} references missing run {run_id}")

    for review in reviews:
        review_id = review.get("review_id")
        if review.get("experiment_run_id") not in run_ids:
            raise ValueError(f"Experiment review {review_id} references missing run {review.get('experiment_run_id')}")
        if review.get("reviewer_slot_id") not in slot_ids:
            raise ValueError(f"Experiment review {review_id} references missing slot {review.get('reviewer_slot_id')}")
        follow_up_task_id = review.get("follow_up_task_id")
        if follow_up_task_id and follow_up_task_id not in task_ids:
            raise ValueError(f"Experiment review {review_id} references missing task {follow_up_task_id}")
        for comparison_id in review.get("comparison_ids", []):
            if comparison_id not in comparison_ids:
                raise ValueError(f"Experiment review {review_id} references missing comparison {comparison_id}")


def _validate_approvals(layout: ProjectLayout, slot_ids: Set[str], task_ids: Set[str]) -> None:
    for approval in _json_records(layout.state_dir / "approvals"):
        approval_id = approval.get("approval_id")
        requested_by_slot_id = approval.get("requested_by_slot_id")
        if requested_by_slot_id and requested_by_slot_id not in slot_ids:
            raise ValueError(f"Approval {approval_id} references missing slot {requested_by_slot_id}")
        scope_type = approval.get("scope_type")
        scope_id = approval.get("scope_id")
        if scope_type == "task_budget_override" and scope_id not in task_ids:
            raise ValueError(f"Approval {approval_id} references missing task {scope_id}")
        if scope_type in {"retire_senior", "retire_junior"} and scope_id not in slot_ids:
            raise ValueError(f"Approval {approval_id} references missing slot {scope_id}")
        if scope_type == "add_junior":
            parent_slot_id = (approval.get("request_payload") or {}).get("parent_slot_id")
            if parent_slot_id not in slot_ids:
                raise ValueError(f"Approval {approval_id} references missing slot {parent_slot_id}")


def validate_integrity(layout: ProjectLayout) -> None:
    _validate_artifacts(layout)
    provisional_task_ids = {str(task.get("task_id")) for task in _json_records(layout.state_dir / "tasks")}
    provisional_slot_ids = {str(slot.get("slot_id")) for slot in _json_records(layout.state_dir / "slots")}
    activation_ids = _validate_activations(layout, provisional_slot_ids, provisional_task_ids)
    slot_ids = _validate_slots(layout, activation_ids)
    task_ids = _validate_tasks(layout, slot_ids, activation_ids)
    _validate_experiments(layout, slot_ids, task_ids, activation_ids)
    _validate_approvals(layout, slot_ids, task_ids)


def prepare_project_for_command_locked(layout: ProjectLayout) -> MigrationPreparation:
    preparation = prepare_project_schema_locked(layout)
    warnings = list(preparation.warnings)

    raw_health = read_json(layout.adapter_health) if layout.adapter_health.exists() else default_adapter_health()
    adapter_health = normalize_adapter_health(raw_health)
    if adapter_health != raw_health:
        write_json_atomic(layout.adapter_health, adapter_health)
        warnings.append("normalized adapter health: state/adapters/health.json")
    preparation.adapter_health = adapter_health

    slot_ids: list[str] = []
    topology_path = layout.topology_state
    if topology_path.exists():
        topology = read_json(topology_path)
        slot_ids = list(topology.get("active_slot_ids", [])) + list(topology.get("retired_slot_ids", []))
    warnings.extend(_repair_support_surfaces(layout, slot_ids))
    try:
        validate_integrity(layout)
    except (ValueError, json.JSONDecodeError) as exc:
        raise CommandError("preflight_integrity_error", str(exc)) from exc

    warnings.extend(adapter_health_warnings(adapter_health))
    preparation.warnings = warnings
    return preparation
