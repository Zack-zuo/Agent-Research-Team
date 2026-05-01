from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from research_agent_team.application.artifact_service import index_artifact
from research_agent_team.application.errors import CommandError
from research_agent_team.application.project_service import _read_slot, _require_string, _write_slot, emit_event, load_project, load_topology
from research_agent_team.application.task_service import (
    _budget_override,
    _effective_budget_envelope,
    _requester_can_assign,
    _slot_is_executable,
    _task_summary,
    _validate_input_path_roots,
)
from research_agent_team.application.visibility_service import resolve_attached_artifacts
from research_agent_team.domain import (
    AgentActivation,
    AgentSlot,
    ApprovalApproverType,
    ApprovalScopeType,
    ArtifactVisibility,
    ExperimentComparison,
    ExperimentRequest,
    ExperimentReview,
    ExperimentRun,
    ProjectStatus,
    SlotRole,
    SlotStatus,
    Task,
    TaskStatus,
)
from research_agent_team.integrations.experiments import LocalFileExperimentAdapter
from research_agent_team.shared import new_id, now_utc
from research_agent_team.storage import ProjectLayout, project_lock, read_json, write_json_atomic, write_text_atomic


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


def _object(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = payload.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CommandError("invalid_payload", f"{key} must be an object", field=key)
    return dict(value)


def _optional_string(payload: Dict[str, Any], key: str) -> Optional[str]:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CommandError("invalid_payload", f"{key} must be a non-empty string when provided", field=key)
    return value.strip()


def _read_experiment_request(layout: ProjectLayout, experiment_request_id: str) -> ExperimentRequest:
    path = layout.experiment_request_state_path(experiment_request_id)
    if not path.exists():
        raise CommandError(
            "experiment_request_not_found",
            f"Experiment request does not exist: {experiment_request_id}",
            experiment_request_id=experiment_request_id,
        )
    return ExperimentRequest.from_dict(read_json(path))


def _write_experiment_request(layout: ProjectLayout, request: ExperimentRequest) -> None:
    write_json_atomic(layout.experiment_request_state_path(request.experiment_request_id), request.to_dict())


def _read_experiment_run(layout: ProjectLayout, experiment_run_id: str) -> ExperimentRun:
    path = layout.experiment_run_state_path(experiment_run_id)
    if not path.exists():
        raise CommandError(
            "experiment_run_not_found",
            f"Experiment run does not exist: {experiment_run_id}",
            experiment_run_id=experiment_run_id,
        )
    return ExperimentRun.from_dict(read_json(path))


def _write_experiment_run(layout: ProjectLayout, run: ExperimentRun) -> None:
    write_json_atomic(layout.experiment_run_state_path(run.experiment_run_id), run.to_dict())


def _read_experiment_comparison(layout: ProjectLayout, comparison_id: str) -> ExperimentComparison:
    path = layout.experiment_comparison_state_path(comparison_id)
    if not path.exists():
        raise CommandError("experiment_comparison_not_found", f"Experiment comparison does not exist: {comparison_id}", comparison_id=comparison_id)
    return ExperimentComparison.from_dict(read_json(path))


def _write_experiment_comparison(layout: ProjectLayout, comparison: ExperimentComparison) -> None:
    write_json_atomic(layout.experiment_comparison_state_path(comparison.comparison_id), comparison.to_dict())


def _write_experiment_review(layout: ProjectLayout, review: ExperimentReview) -> None:
    write_json_atomic(layout.experiment_review_state_path(review.review_id), review.to_dict())


def _list_experiment_reviews(layout: ProjectLayout) -> List[ExperimentReview]:
    reviews_dir = layout.state_dir / "experiments" / "reviews"
    if not reviews_dir.exists():
        return []
    return [ExperimentReview.from_dict(read_json(path)) for path in sorted(reviews_dir.glob("*.json"))]


def _find_experiment_request_by_task_id(layout: ProjectLayout, task_id: str) -> Optional[ExperimentRequest]:
    requests_dir = layout.state_dir / "experiments" / "requests"
    if not requests_dir.exists():
        return None
    for path in sorted(requests_dir.glob("*.json")):
        request = ExperimentRequest.from_dict(read_json(path))
        if request.task_id == task_id:
            return request
    return None


def _find_experiment_run_by_task_id(layout: ProjectLayout, task_id: str) -> Optional[ExperimentRun]:
    runs_dir = layout.state_dir / "experiments" / "runs"
    if not runs_dir.exists():
        return None
    for path in sorted(runs_dir.glob("*.json")):
        run = ExperimentRun.from_dict(read_json(path))
        if run.task_id == task_id:
            return run
    return None


def _experiment_pair_for_task(layout: ProjectLayout, task_id: str) -> Tuple[Optional[ExperimentRequest], Optional[ExperimentRun]]:
    return _find_experiment_request_by_task_id(layout, task_id), _find_experiment_run_by_task_id(layout, task_id)


def _read_adapter_config(layout: ProjectLayout) -> Dict[str, Any]:
    if not layout.adapter_config.exists():
        return {}
    config = read_json(layout.adapter_config)
    experiment_config = config.get("experiments", config.get("experiment", {}))
    return experiment_config if isinstance(experiment_config, dict) else {}


def _write_experiment_health(layout: ProjectLayout, *, status: str, message: str) -> None:
    health = read_json(layout.adapter_health) if layout.adapter_health.exists() else {}
    health["experiments"] = {"status": status, "message": message, "updated_at": now_utc()}
    write_json_atomic(layout.adapter_health, health)


def _resolve_experiment_adapter(layout: ProjectLayout) -> LocalFileExperimentAdapter:
    config = _read_adapter_config(layout)
    if config.get("enabled") is False:
        message = "Experiment adapter is disabled."
        _write_experiment_health(layout, status="degraded", message=message)
        raise CommandError("experiment_adapter_unavailable", message)
    adapter_name = config.get("adapter", config.get("type", "local_file"))
    if adapter_name != "local_file":
        message = f"Experiment adapter is unavailable: {adapter_name}"
        _write_experiment_health(layout, status="degraded", message=message)
        raise CommandError("experiment_adapter_unavailable", message, adapter=adapter_name)
    _write_experiment_health(layout, status="healthy", message="Local-file experiment adapter ready.")
    return LocalFileExperimentAdapter()


def _build_task_description(payload: Dict[str, Any]) -> str:
    return "\n".join(
        [
            _require_string(payload, "objective"),
            "",
            "## Hypothesis",
            str(payload.get("hypothesis") or ""),
            "",
            "## Method",
            _require_string(payload, "method"),
            "",
        ]
    )


def _experiment_run_summary(request: ExperimentRequest, run: ExperimentRun, task: Task) -> Dict[str, Any]:
    return {
        "experiment_run_id": run.experiment_run_id,
        "experiment_request_id": request.experiment_request_id,
        "status": run.status,
        "requester_slot_id": request.requester_slot_id,
        "executor_slot_id": request.executor_slot_id,
        "reviewer_slot_id": request.reviewer_slot_id,
        "task_id": task.task_id,
        "current_activation_id": task.current_activation_id,
        "compare_run_ids": list(request.compare_run_ids),
        "comparison_ids": list(run.comparison_ids),
        "published_artifact_ids": list(run.published_artifact_ids),
        "created_at": run.created_at,
    }


def _comparison_summary(comparison: ExperimentComparison) -> Dict[str, Any]:
    return {
        "comparison_id": comparison.comparison_id,
        "primary_run_id": comparison.primary_run_id,
        "compared_run_ids": list(comparison.compared_run_ids),
        "status": comparison.status,
        "output_artifact_ids": list(comparison.output_artifact_ids),
        "created_at": comparison.created_at,
    }


def _comparison_artifact_ids(layout: ProjectLayout, run: ExperimentRun) -> List[str]:
    artifact_ids: List[str] = []
    for comparison_id in run.comparison_ids:
        artifact_ids.extend(_read_experiment_comparison(layout, comparison_id).output_artifact_ids)
    return artifact_ids


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


def _select_default_executor(layout: ProjectLayout, requester: AgentSlot) -> str:
    for slot_id in requester.descendant_slot_ids:
        try:
            slot = _read_slot(layout, slot_id)
        except CommandError:
            continue
        if slot.role == SlotRole.JUNIOR_PHD and slot.status == SlotStatus.ACTIVE:
            return slot.slot_id
    raise CommandError("experiment_executor_required", "No active junior executor descendant is available")


def _validate_compare_run_ids(layout: ProjectLayout, compare_run_ids: List[str], project_id: str) -> None:
    if len(compare_run_ids) != len(set(compare_run_ids)):
        raise CommandError("invalid_payload", "compare_run_ids must be unique", field="compare_run_ids")
    for run_id in compare_run_ids:
        run = _read_experiment_run(layout, run_id)
        if run.project_id != project_id:
            raise CommandError("invalid_compare_run", f"Compare run belongs to a different project: {run_id}", experiment_run_id=run_id)
        if run.status not in {"awaiting_review", "reviewed"}:
            raise CommandError("invalid_compare_run", f"Compare run is not published: {run_id}", experiment_run_id=run_id)


def _write_request_package(layout: ProjectLayout, request: ExperimentRequest, run: ExperimentRun) -> None:
    package_dir = layout.experiment_queue_root(request.experiment_request_id)
    package_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        package_dir / "request.json",
        {
            "experiment_request_id": request.experiment_request_id,
            "experiment_run_id": run.experiment_run_id,
            "task_id": request.task_id,
            "requester_slot_id": request.requester_slot_id,
            "executor_slot_id": request.executor_slot_id,
            "reviewer_slot_id": request.reviewer_slot_id,
            "title": request.title,
            "objective": request.objective,
            "hypothesis": request.hypothesis,
            "method": request.method,
            "run_parameters": dict(request.run_parameters),
            "compare_run_ids": list(request.compare_run_ids),
            "budget_envelope": dict(request.budget_envelope),
            "run_root": run.run_root,
        },
    )
    success_lines = [f"- {criterion}" for criterion in request.expected_output_types] or ["- Evidence package"]
    write_text_atomic(
        package_dir / "briefing.md",
        "\n".join(
            [
                f"# {request.title}",
                "",
                request.objective,
                "",
                "## Hypothesis",
                request.hypothesis,
                "",
                "## Method",
                request.method,
                "",
                "## Expected Outputs",
                *success_lines,
                "",
            ]
        ),
    )
    if request.compare_run_ids:
        write_json_atomic(package_dir / "compare-targets.json", {"compare_run_ids": list(request.compare_run_ids)})


def mark_experiment_admitted_locked(layout: ProjectLayout, *, task_id: str, activation_id: str) -> None:
    request, run = _experiment_pair_for_task(layout, task_id)
    if request is None or run is None:
        return
    timestamp = now_utc()
    request.status = "admitted"
    request.updated_at = timestamp
    run.activation_id = activation_id
    _write_experiment_request(layout, request)
    _write_experiment_run(layout, run)


def mark_experiment_run_running_locked(layout: ProjectLayout, *, task_id: str, activation_id: str) -> None:
    request, run = _experiment_pair_for_task(layout, task_id)
    if request is None or run is None:
        return
    timestamp = now_utc()
    run.status = "running"
    run.activation_id = activation_id
    run.started_at = run.started_at or timestamp
    _write_experiment_run(layout, run)
    project = load_project(layout)
    emit_event(
        layout,
        project.project_id,
        "experiment.run_started",
        {"experiment_run_id": run.experiment_run_id},
        slot_id=request.executor_slot_id,
        task_id=task_id,
        activation_id=activation_id,
    )


def mark_experiment_run_interrupted_locked(layout: ProjectLayout, *, task_id: str, activation_id: Optional[str]) -> None:
    request, run = _experiment_pair_for_task(layout, task_id)
    if request is None or run is None:
        return
    run.status = "interrupted"
    run.activation_id = activation_id
    run.ended_at = now_utc()
    _write_experiment_run(layout, run)


def mark_experiment_run_cancelled_locked(layout: ProjectLayout, *, task_id: str, activation_id: Optional[str]) -> None:
    request, run = _experiment_pair_for_task(layout, task_id)
    if request is None or run is None:
        return
    request.status = "cancelled"
    request.updated_at = now_utc()
    run.status = "cancelled"
    run.activation_id = activation_id
    run.ended_at = request.updated_at
    _write_experiment_request(layout, request)
    _write_experiment_run(layout, run)


def mark_experiment_budget_decision_locked(layout: ProjectLayout, *, task_id: str, approved: bool) -> None:
    request, run = _experiment_pair_for_task(layout, task_id)
    if request is None or run is None:
        return
    timestamp = now_utc()
    request.current_approval_id = None
    request.updated_at = timestamp
    if approved:
        request.status = "queued"
    else:
        request.status = "cancelled"
        run.status = "cancelled"
        run.ended_at = timestamp
    _write_experiment_request(layout, request)
    _write_experiment_run(layout, run)


def fail_experiment_run_locked(
    layout: ProjectLayout,
    *,
    task_id: str,
    activation_id: Optional[str],
    failure_summary: str,
) -> Optional[str]:
    request, run = _experiment_pair_for_task(layout, task_id)
    if request is None or run is None:
        return None
    timestamp = now_utc()
    failure_path = PurePosixPath(run.run_root) / "failure-summary.md"
    write_text_atomic(
        layout.root / failure_path,
        "\n".join(
            [
                f"# Experiment Failure for {request.title}",
                "",
                f"- Experiment Run: {run.experiment_run_id}",
                f"- Task: {task_id}",
                f"- Activation: {activation_id or 'none'}",
                "",
                "## Failure Summary",
                failure_summary,
                "",
            ]
        ),
    )
    artifact = index_artifact(
        layout,
        path=str(failure_path),
        artifact_type="experiment_failure_summary",
        visibility=ArtifactVisibility.PROJECT_SHARED,
        producing_slot_id=request.executor_slot_id,
        producing_activation_id=activation_id,
        task_id=task_id,
        source_artifact_ids=list(run.published_artifact_ids),
    )
    run.status = "failed"
    run.activation_id = activation_id
    run.failure_artifact_id = artifact["artifact_id"]
    run.ended_at = timestamp
    _write_experiment_run(layout, run)
    project = load_project(layout)
    emit_event(
        layout,
        project.project_id,
        "experiment.run_failed",
        {"experiment_run_id": run.experiment_run_id, "artifact_id": artifact["artifact_id"]},
        slot_id=request.executor_slot_id,
        task_id=task_id,
        activation_id=activation_id,
    )
    return artifact["artifact_id"]


def publish_experiment_run_locked(layout: ProjectLayout, *, activation: AgentActivation, task: Task) -> List[str]:
    request, run = _experiment_pair_for_task(layout, task.task_id)
    if request is None or run is None:
        return []
    adapter = _resolve_experiment_adapter(layout)
    run_dir = layout.root / run.run_root
    outputs_dir = run_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    prepared_payload = adapter.prepare(task_id=task.task_id, bundle_id=activation.bundle_id, experiment_request=request.to_dict())
    result_payload = adapter.run(experiment_request=request.to_dict(), budget_envelope=task.budget_envelope)
    publish_payload = adapter.publish(experiment_run_id=run.experiment_run_id, output_dir=str(run_dir))

    request_path = PurePosixPath(run.run_root) / "request.json"
    prepared_path = PurePosixPath(run.run_root) / "prepared.json"
    result_path = PurePosixPath(run.run_root) / "outputs" / "result.json"
    summary_path = PurePosixPath(run.run_root) / "outputs" / "summary.md"
    manifest_path = PurePosixPath(run.run_root) / "publish-manifest.json"

    write_json_atomic(layout.root / request_path, request.to_dict())
    write_json_atomic(layout.root / prepared_path, prepared_payload)
    write_json_atomic(layout.root / result_path, result_payload)
    write_text_atomic(
        layout.root / summary_path,
        "\n".join(
            [
                f"# {request.title}",
                "",
                f"- Adapter: {adapter.adapter_type}",
                f"- Objective: {request.objective}",
                f"- Hypothesis: {request.hypothesis}",
                "",
                "## Run Parameters",
                f"`{request.run_parameters}`",
                "",
            ]
        ),
    )
    timestamp = now_utc()
    write_json_atomic(
        layout.root / manifest_path,
        {
            "experiment_run_id": run.experiment_run_id,
            "adapter_type": adapter.adapter_type,
            "published_at": timestamp,
            "publish_payload": publish_payload,
        },
    )

    published_artifact_ids: List[str] = []
    for path, artifact_type in (
        (request_path, "experiment_request"),
        (prepared_path, "experiment_prepared"),
        (result_path, "experiment_result"),
        (summary_path, "experiment_summary_markdown"),
        (manifest_path, "experiment_publish_manifest"),
    ):
        artifact = index_artifact(
            layout,
            path=str(path),
            artifact_type=artifact_type,
            visibility=ArtifactVisibility.PROJECT_SHARED,
            producing_slot_id=request.executor_slot_id,
            producing_activation_id=activation.activation_id,
            task_id=task.task_id,
            source_artifact_ids=list(request.input_artifact_ids),
            created_at=timestamp,
        )
        published_artifact_ids.append(artifact["artifact_id"])

    comparison_ids: List[str] = []
    if request.compare_run_ids:
        comparison_id = new_id("comparison")
        comparison_dir = run_dir / "comparisons"
        comparison_dir.mkdir(parents=True, exist_ok=True)
        comparison_json_path = PurePosixPath(run.run_root) / "comparisons" / f"{comparison_id}.json"
        comparison_md_path = PurePosixPath(run.run_root) / "comparisons" / f"{comparison_id}.md"
        comparison_payload = adapter.compare(primary_run_id=run.experiment_run_id, compared_run_ids=request.compare_run_ids)
        write_json_atomic(layout.root / comparison_json_path, comparison_payload)
        write_text_atomic(
            layout.root / comparison_md_path,
            "\n".join(
                [
                    f"# Comparison for {request.title}",
                    "",
                    f"- Comparison ID: {comparison_id}",
                    f"- Primary Run: {run.experiment_run_id}",
                    f"- Compared Runs: {', '.join(request.compare_run_ids)}",
                    "",
                ]
            ),
        )
        comparison_artifact_ids: List[str] = []
        for path, artifact_type in (
            (comparison_json_path, "experiment_comparison"),
            (comparison_md_path, "experiment_comparison_markdown"),
        ):
            artifact = index_artifact(
                layout,
                path=str(path),
                artifact_type=artifact_type,
                visibility=ArtifactVisibility.PROJECT_SHARED,
                producing_slot_id=request.executor_slot_id,
                producing_activation_id=activation.activation_id,
                task_id=task.task_id,
                source_artifact_ids=list(published_artifact_ids),
                created_at=timestamp,
            )
            comparison_artifact_ids.append(artifact["artifact_id"])
        comparison = ExperimentComparison(
            comparison_id=comparison_id,
            primary_run_id=run.experiment_run_id,
            compared_run_ids=list(request.compare_run_ids),
            status="completed",
            output_artifact_ids=comparison_artifact_ids,
            created_at=timestamp,
            completed_at=timestamp,
            failure_summary=None,
        )
        _write_experiment_comparison(layout, comparison)
        comparison_ids.append(comparison.comparison_id)

    run.status = "awaiting_review"
    run.activation_id = activation.activation_id
    run.adapter_type = adapter.adapter_type
    run.prepared_manifest_path = str(prepared_path)
    run.published_artifact_ids = published_artifact_ids
    run.comparison_ids = comparison_ids
    run.consumed_budget = dict(activation.consumed_budget)
    run.published_at = timestamp
    run.ended_at = timestamp
    _write_experiment_run(layout, run)
    project = load_project(layout)
    emit_event(
        layout,
        project.project_id,
        "experiment.run_published",
        {"experiment_run_id": run.experiment_run_id, "artifact_count": len(published_artifact_ids)},
        slot_id=request.executor_slot_id,
        task_id=task.task_id,
        activation_id=activation.activation_id,
    )
    return published_artifact_ids


def run_experiment(payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_payload(payload)
    requester_slot_id = _require_string(payload, "requester_slot_id")
    title = _require_string(payload, "title")
    objective = _require_string(payload, "objective")
    hypothesis = str(payload.get("hypothesis") or "")
    method = _require_string(payload, "method")
    success_criteria = _string_list(payload, "success_criteria", required=True)
    input_artifact_ids = _string_list(payload, "input_artifact_ids")
    input_path_roots = _string_list(payload, "input_path_roots")
    expected_output_types = _string_list(payload, "expected_output_types")
    compare_run_ids = _string_list(payload, "compare_run_ids")
    run_parameters = _object(payload, "run_parameters")
    budget_override = _budget_override(payload)

    with project_lock(layout.lock_path):
        from research_agent_team.application.health_service import prepare_project_for_command_locked
        from research_agent_team.application.activation_service import _admit_next_task_if_possible_locked, _write_task
        from research_agent_team.application.approval_service import create_approval
        from research_agent_team.application.recovery_service import recover_stale_activations_locked

        preparation = prepare_project_for_command_locked(layout)
        project = load_project(layout)
        if project.status == ProjectStatus.ARCHIVED:
            raise CommandError("invalid_project_status", "Cannot run experiments in an archived project", status=project.status.value)
        requester = _read_slot(layout, requester_slot_id)
        executor_slot_id = _optional_string(payload, "executor_slot_id") or _select_default_executor(layout, requester)
        executor = _read_slot(layout, executor_slot_id)
        recover_stale_activations_locked(layout, target_slot_ids={executor_slot_id})
        executor = _read_slot(layout, executor_slot_id)
        if requester.status != SlotStatus.ACTIVE or executor.status != SlotStatus.ACTIVE:
            raise CommandError("invalid_slot_status", "Requester and executor slots must both be active")
        if requester.role != SlotRole.SENIOR_PHD:
            raise CommandError("invalid_requester_role", "Experiment requester must be an active senior slot", requester_slot_id=requester_slot_id)
        if executor.role != SlotRole.JUNIOR_PHD:
            raise CommandError("invalid_executor_role", "Experiment executor must be an active junior slot", executor_slot_id=executor_slot_id)
        if executor.slot_id not in requester.descendant_slot_ids:
            raise CommandError("executor_not_descendant", "Experiment executor must be a descendant of the requester", executor_slot_id=executor.slot_id)
        if not _slot_is_executable(layout, executor):
            raise CommandError("owner_not_executable", "Executor slot is not executable under policy", executor_slot_id=executor.slot_id)

        resolve_attached_artifacts(layout, requester_slot_id, executor_slot_id, input_artifact_ids)
        _validate_input_path_roots(layout, executor_slot_id, input_path_roots)
        _validate_compare_run_ids(layout, compare_run_ids, project.project_id)
        adapter = _resolve_experiment_adapter(layout)
        budget_envelope = _effective_budget_envelope(layout, executor, budget_override)
        if "experiment_runs" not in budget_override:
            current_experiment_runs = budget_envelope.get("experiment_runs")
            budget_envelope["experiment_runs"] = min(current_experiment_runs, 1.0) if current_experiment_runs is not None else 1.0
        timestamp = now_utc()
        task = Task(
            task_id=new_id("task"),
            project_id=project.project_id,
            requester_slot_id=requester_slot_id,
            owner_slot_id=executor_slot_id,
            status=TaskStatus.QUEUED,
            title=title,
            description=_build_task_description(payload),
            success_criteria=success_criteria,
            input_artifact_ids=input_artifact_ids,
            input_path_roots=input_path_roots,
            expected_output_types=expected_output_types,
            budget_envelope=budget_envelope,
            review_requirement="experiment_review",
            created_at=timestamp,
            updated_at=timestamp,
            approval_policy_ref=project.policy_refs.get("approvals"),
        )
        request = ExperimentRequest(
            experiment_request_id=new_id("experiment-request"),
            project_id=project.project_id,
            requester_slot_id=requester_slot_id,
            executor_slot_id=executor_slot_id,
            reviewer_slot_id=requester_slot_id,
            task_id=task.task_id,
            status="queued",
            title=title,
            objective=objective,
            hypothesis=hypothesis,
            method=method,
            run_parameters=run_parameters,
            input_artifact_ids=input_artifact_ids,
            input_path_roots=input_path_roots,
            expected_output_types=expected_output_types,
            compare_run_ids=compare_run_ids,
            budget_envelope=budget_envelope,
            created_at=timestamp,
            updated_at=timestamp,
        )
        experiment_run_id = new_id("experiment-run")
        run = ExperimentRun(
            experiment_run_id=experiment_run_id,
            experiment_request_id=request.experiment_request_id,
            project_id=project.project_id,
            task_id=task.task_id,
            status="prepared",
            adapter_type=adapter.adapter_type,
            run_root=str(PurePosixPath("experiments") / "runs" / experiment_run_id),
            created_at=timestamp,
        )
        _write_task(layout, task)
        _write_experiment_request(layout, request)
        _write_experiment_run(layout, run)
        _write_request_package(layout, request, run)

        pending_approval = None
        launch_request = None
        approval_id = None
        budget_policy = read_json(layout.state_dir / "policies" / "budget.json")
        if budget_override and bool(budget_policy.get("requires_approval_for_override", True)):
            task.status = TaskStatus.AWAITING_APPROVAL
            request.status = "awaiting_approval"
            approval = create_approval(
                layout,
                scope_type=ApprovalScopeType.TASK_BUDGET_OVERRIDE,
                scope_id=task.task_id,
                requested_by_slot_id=requester_slot_id,
                approver_type=ApprovalApproverType.SUPERVISOR,
                reason="Budget override requires approval before experiment admission.",
                request_payload={"task_id": task.task_id, "owner_slot_id": executor_slot_id},
                task_id=task.task_id,
                slot_id=executor_slot_id,
            )
            approval_id = approval["approval_id"]
            task.current_approval_id = approval_id
            request.current_approval_id = approval_id
            task.updated_at = timestamp
            request.updated_at = timestamp
            _write_task(layout, task)
            _write_experiment_request(layout, request)
            if task.task_id not in executor.active_task_ids:
                executor.active_task_ids.append(task.task_id)
            executor.updated_at = timestamp
            _write_slot(layout, executor)
            pending_approval = _approval_summary(approval)
        else:
            executor.queued_task_ids.append(task.task_id)
            executor.updated_at = timestamp
            _write_slot(layout, executor)
            launch_request = _admit_next_task_if_possible_locked(layout, project.status, executor)

        hook_warnings = emit_event(
            layout,
            project.project_id,
            "task.created",
            {"requester_slot_id": requester_slot_id, "title": title},
            slot_id=executor_slot_id,
            task_id=task.task_id,
            approval_id=approval_id,
            dispatch_hooks=True,
        )
        hook_warnings.extend(emit_event(
            layout,
            project.project_id,
            "experiment.requested",
            {"experiment_request_id": request.experiment_request_id, "experiment_run_id": run.experiment_run_id},
            slot_id=executor_slot_id,
            task_id=task.task_id,
            approval_id=approval_id,
            dispatch_hooks=True,
        ))

        from research_agent_team.application.activation_service import _read_task
        from research_agent_team.application.reporting_service import rebuild_slot_views

        rebuild_slot_views(layout, {requester_slot_id, executor_slot_id, "supervisor"})
        task = _read_task(layout, task.task_id)
        request = _read_experiment_request(layout, request.experiment_request_id)
        run = _read_experiment_run(layout, run.experiment_run_id)
        executor = _read_slot(layout, executor_slot_id)
        return {
            "experiment_request_id": request.experiment_request_id,
            "experiment_run": _experiment_run_summary(request, run, task),
            "task": _task_summary(task, executor),
            "admitted": launch_request is not None and task.status == TaskStatus.ADMITTED,
            "launch_request": launch_request,
            "owner_queue_depth": len(executor.queued_task_ids),
            "pending_approval": pending_approval,
            "warnings": preparation.warnings + hook_warnings,
        }


def review_experiment(payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_payload(payload)
    experiment_run_id = _require_string(payload, "experiment_run_id")
    reviewer_slot_id = _require_string(payload, "reviewer_slot_id")
    outcome = _require_string(payload, "outcome")
    if outcome not in {"accepted", "needs_follow_up"}:
        raise CommandError("invalid_payload", "outcome must be accepted or needs_follow_up", field="outcome")
    decision_summary = _require_string(payload, "decision_summary")

    with project_lock(layout.lock_path):
        from research_agent_team.application.health_service import prepare_project_for_command_locked
        from research_agent_team.application.activation_service import _admit_next_task_if_possible_locked, _read_task, _write_task
        from research_agent_team.application.reporting_service import rebuild_slot_views

        preparation = prepare_project_for_command_locked(layout)
        project = load_project(layout)
        run = _read_experiment_run(layout, experiment_run_id)
        request = _read_experiment_request(layout, run.experiment_request_id)
        task = _read_task(layout, run.task_id)
        if run.status != "awaiting_review":
            raise CommandError("experiment_run_not_reviewable", "Experiment run must be awaiting_review", experiment_run_id=experiment_run_id)
        if reviewer_slot_id != request.reviewer_slot_id and reviewer_slot_id != "supervisor":
            raise CommandError("reviewer_not_authorized", "Reviewer is not authorized for this experiment run", reviewer_slot_id=reviewer_slot_id)
        if any(review.experiment_run_id == run.experiment_run_id for review in _list_experiment_reviews(layout)):
            raise CommandError("experiment_already_reviewed", "Experiment run has already been reviewed", experiment_run_id=experiment_run_id)

        if outcome == "accepted":
            forbidden_fields = ["follow_up_owner_slot_id", "follow_up_title", "follow_up_description"]
            if any(payload.get(field) for field in forbidden_fields) or _string_list(payload, "follow_up_success_criteria"):
                raise CommandError("invalid_payload", "Accepted reviews must not include follow-up fields")
        else:
            if project.status == ProjectStatus.ARCHIVED:
                raise CommandError("invalid_project_status", "Cannot assign follow-up work in an archived project", status=project.status.value)
            owner_slot_id = _require_string(payload, "follow_up_owner_slot_id")
            follow_up_title = _require_string(payload, "follow_up_title")
            follow_up_description = _require_string(payload, "follow_up_description")
            follow_up_success_criteria = _string_list(payload, "follow_up_success_criteria", required=True)
            topology = load_topology(layout)
            reviewer = _read_slot(layout, reviewer_slot_id)
            follow_up_owner = _read_slot(layout, owner_slot_id)
            if reviewer.status != SlotStatus.ACTIVE or follow_up_owner.status != SlotStatus.ACTIVE:
                raise CommandError("invalid_slot_status", "Reviewer and follow-up owner slots must both be active")
            if owner_slot_id not in topology.active_slot_ids:
                raise CommandError("invalid_slot_status", "Follow-up owner slot must be active in the current topology", slot_id=owner_slot_id)
            if not _requester_can_assign(reviewer, follow_up_owner):
                raise CommandError("requester_not_authorized", "Reviewer cannot assign follow-up work to this owner slot")
            if not _slot_is_executable(layout, follow_up_owner):
                raise CommandError("owner_not_executable", "Follow-up owner slot is not executable under policy", owner_slot_id=owner_slot_id)

        timestamp = now_utc()
        review_id = new_id("review")
        review_path = f"shared/reports/experiment-review-{review_id}.md"
        write_text_atomic(
            layout.root / review_path,
            "\n".join(
                [
                    f"# Review for {request.title}",
                    "",
                    f"- Experiment Run: {run.experiment_run_id}",
                    f"- Reviewer: {reviewer_slot_id}",
                    f"- Outcome: {outcome}",
                    "",
                    "## Decision",
                    decision_summary,
                    "",
                ]
            ),
        )
        source_artifact_ids = list(run.published_artifact_ids) + _comparison_artifact_ids(layout, run)
        review_artifact = index_artifact(
            layout,
            path=review_path,
            artifact_type="experiment_review",
            visibility=ArtifactVisibility.PROJECT_SHARED,
            producing_slot_id=reviewer_slot_id,
            task_id=task.task_id,
            source_artifact_ids=source_artifact_ids,
            created_at=timestamp,
        )
        review = ExperimentReview(
            review_id=review_id,
            experiment_run_id=run.experiment_run_id,
            reviewer_slot_id=reviewer_slot_id,
            outcome=outcome,
            decision_summary=decision_summary,
            review_artifact_id=review_artifact["artifact_id"],
            comparison_ids=list(run.comparison_ids),
            follow_up_task_id=None,
            created_at=timestamp,
        )

        follow_up_task_summary = None
        follow_up_launch_request = None
        if outcome == "needs_follow_up":
            owner = follow_up_owner
            follow_up_task = Task(
                task_id=new_id("task"),
                project_id=project.project_id,
                requester_slot_id=reviewer_slot_id,
                owner_slot_id=owner_slot_id,
                status=TaskStatus.QUEUED,
                title=follow_up_title,
                description=follow_up_description,
                success_criteria=follow_up_success_criteria,
                input_artifact_ids=[review_artifact["artifact_id"], *run.published_artifact_ids, *_comparison_artifact_ids(layout, run)],
                input_path_roots=[],
                expected_output_types=[],
                budget_envelope=_effective_budget_envelope(layout, owner, {}),
                review_requirement="none",
                created_at=timestamp,
                updated_at=timestamp,
                approval_policy_ref=project.policy_refs.get("approvals"),
            )
            _write_task(layout, follow_up_task)
            owner.queued_task_ids.append(follow_up_task.task_id)
            owner.updated_at = timestamp
            _write_slot(layout, owner)
            hook_warnings = emit_event(
                layout,
                project.project_id,
                "task.created",
                {"requester_slot_id": reviewer_slot_id, "title": follow_up_task.title},
                slot_id=owner_slot_id,
                task_id=follow_up_task.task_id,
                dispatch_hooks=True,
            )
            follow_up_launch_request = _admit_next_task_if_possible_locked(layout, project.status, owner)
            owner = _read_slot(layout, owner_slot_id)
            follow_up_task = _read_task(layout, follow_up_task.task_id)
            follow_up_task_summary = _task_summary(follow_up_task, owner)
            review.follow_up_task_id = follow_up_task.task_id
        else:
            hook_warnings = []

        run.status = "reviewed"
        run.reviewed_at = timestamp
        _write_experiment_run(layout, run)
        task.status = TaskStatus.COMPLETED
        task.completed_at = timestamp
        task.updated_at = timestamp
        _write_task(layout, task)
        executor = _read_slot(layout, request.executor_slot_id)
        if task.task_id not in executor.completed_task_ids:
            executor.completed_task_ids.append(task.task_id)
        executor.updated_at = timestamp
        _write_slot(layout, executor)
        _write_experiment_review(layout, review)
        hook_warnings.extend(emit_event(
            layout,
            project.project_id,
            "experiment.review_completed",
            {"experiment_run_id": run.experiment_run_id, "outcome": outcome},
            slot_id=reviewer_slot_id,
            task_id=task.task_id,
            activation_id=run.activation_id,
            dispatch_hooks=True,
        ))
        rebuild_slot_views(layout, {request.requester_slot_id, request.executor_slot_id, reviewer_slot_id, "supervisor"})
        return {
            "experiment_run": _experiment_run_summary(request, run, task),
            "comparisons": [_comparison_summary(_read_experiment_comparison(layout, comparison_id)) for comparison_id in run.comparison_ids],
            "review": {
                "review_id": review.review_id,
                "experiment_run_id": review.experiment_run_id,
                "outcome": review.outcome,
                "review_artifact_id": review.review_artifact_id,
                "follow_up_task_id": review.follow_up_task_id,
                "created_at": review.created_at,
            },
            "follow_up_task": follow_up_task_summary,
            "follow_up_launch_request": follow_up_launch_request,
            "pending_approval": None,
            "warnings": preparation.warnings + hook_warnings,
        }
