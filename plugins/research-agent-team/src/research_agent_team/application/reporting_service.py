from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from research_agent_team.application.artifact_service import artifact_summary, index_artifact, list_artifacts, recent_artifacts
from research_agent_team.application.errors import CommandError
from research_agent_team.application.project_service import (
    _project_summary,
    _read_slot,
    _require_string,
    _slot_summary,
    _topology_summary,
    emit_event,
    load_project,
    load_topology,
    read_all_slots,
)
from research_agent_team.domain import ApprovalApproverType, ApprovalScopeType, ArtifactVisibility
from research_agent_team.shared import now_utc
from research_agent_team.storage import ProjectLayout, ensure_support_surfaces, project_lock, read_json, write_text_atomic


REPORT_TYPES = {
    "status",
    "topology",
    "pending_approvals",
    "next_steps",
    "literature_review",
    "experiment_summary",
    "final_package",
}

SCOPE_TYPES = {"project", "slot", "task", "experiment_run"}


def _layout_from_payload(payload: Dict[str, Any]) -> ProjectLayout:
    return ProjectLayout(Path(_require_string(payload, "root_path")))


def _safe_timestamp(timestamp: str) -> str:
    return timestamp.replace(":", "").replace("-", "").replace(".", "").replace("+", "").replace("Z", "Z")


def _report_slug(report_type: str) -> str:
    return report_type.replace("_", "-")


def _read_json_files(directory: Path) -> List[Dict[str, Any]]:
    if not directory.exists():
        return []
    records: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            records.append(read_json(path))
        except (json.JSONDecodeError, ValueError):
            continue
    return records


def _tasks(layout: ProjectLayout) -> List[Dict[str, Any]]:
    return _read_json_files(layout.state_dir / "tasks")


def _activations(layout: ProjectLayout) -> List[Dict[str, Any]]:
    return _read_json_files(layout.state_dir / "activations")


def _approvals(layout: ProjectLayout) -> List[Dict[str, Any]]:
    return _read_json_files(layout.state_dir / "approvals")


def _pending_approvals(layout: ProjectLayout) -> List[Dict[str, Any]]:
    return [approval for approval in _approvals(layout) if approval.get("status") == "pending"]


def _task_counts(layout: ProjectLayout) -> Dict[str, int]:
    counts = {"active": 0, "queued": 0, "blocked": 0, "completed": 0}
    active_statuses = {"admitted", "running", "awaiting_review", "awaiting_approval"}
    for task in _tasks(layout):
        status = task.get("status")
        if status == "queued":
            counts["queued"] += 1
        elif status == "blocked":
            counts["blocked"] += 1
        elif status == "completed":
            counts["completed"] += 1
        elif status in active_statuses:
            counts["active"] += 1
    return counts


def _budget_summary(layout: ProjectLayout) -> Dict[str, Any]:
    budget = read_json(layout.budget_state) if layout.budget_state.exists() else {}
    hard_stop_activation_ids = sorted(
        activation["activation_id"]
        for activation in _activations(layout)
        if activation.get("pending_stop_reason") == "budget_hard_limit"
    )
    return {
        "consumed_to_date": budget.get("consumed_to_date", {}),
        "thresholds": budget.get("thresholds", {}),
        "last_recomputed_at": budget.get("last_recomputed_at"),
        "hard_stop_activation_ids": hard_stop_activation_ids,
    }


def _adapter_health(layout: ProjectLayout) -> Dict[str, Any]:
    return read_json(layout.adapter_health) if layout.adapter_health.exists() else {}


def _approval_summary(approval: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "approval_id": approval.get("approval_id"),
        "scope_type": approval.get("scope_type"),
        "scope_id": approval.get("scope_id"),
        "approver_type": approval.get("approver_type"),
        "status": approval.get("status"),
        "reason": approval.get("reason"),
        "created_at": approval.get("created_at"),
    }


def _render_list(lines: List[str], values: Iterable[str]) -> None:
    emitted = False
    for value in values:
        lines.append(f"- {value}")
        emitted = True
    if not emitted:
        lines.append("- none")


def rebuild_slot_views(layout: ProjectLayout, slot_ids: Optional[Iterable[str]] = None) -> None:
    slots = read_all_slots(layout)
    tasks = _tasks(layout)
    approvals = _pending_approvals(layout)
    artifacts = list_artifacts(layout)
    target_slot_ids = list(slot_ids or slots.keys())

    for slot_id in target_slot_ids:
        if slot_id not in slots:
            continue
        slot = slots[slot_id]
        owned_tasks = [
            task
            for task in tasks
            if task.get("owner_slot_id") == slot_id and task.get("status") in {"queued", "admitted", "running", "awaiting_approval", "blocked"}
        ]
        requested_tasks = [
            task
            for task in tasks
            if task.get("requester_slot_id") == slot_id and task.get("status") not in {"completed", "failed", "cancelled"}
        ]
        reviewable_approvals = approvals if slot_id == "supervisor" else []
        requested_approvals = [approval for approval in approvals if approval.get("requested_by_slot_id") == slot_id]
        produced_artifacts = [artifact for artifact in artifacts if artifact.get("producing_slot_id") == slot_id][-5:]

        inbox_lines = [
            f"# Inbox for {slot_id}",
            "",
            "## Active Work",
        ]
        _render_list(
            inbox_lines,
            [f"{task.get('task_id')}: {task.get('status')} - {task.get('title')}" for task in owned_tasks],
        )
        inbox_lines.extend(["", "## Pending Approvals To Review"])
        _render_list(
            inbox_lines,
            [f"{approval.get('approval_id')}: {approval.get('scope_type')} - {approval.get('reason')}" for approval in reviewable_approvals],
        )
        inbox_lines.extend(["", "## Current Activation", f"- {slot.current_activation_id}" if slot.current_activation_id else "- none"])

        outbox_lines = [
            f"# Outbox for {slot_id}",
            "",
            "## Requested Work",
        ]
        _render_list(
            outbox_lines,
            [f"{task.get('task_id')}: {task.get('status')} - {task.get('title')}" for task in requested_tasks],
        )
        outbox_lines.extend(["", "## Pending Requests"])
        _render_list(
            outbox_lines,
            [f"{approval.get('approval_id')}: {approval.get('scope_type')} - {approval.get('reason')}" for approval in requested_approvals],
        )
        outbox_lines.extend(["", "## Recent Artifacts"])
        _render_list(outbox_lines, [f"{artifact.get('artifact_id')}: {artifact.get('path')}" for artifact in produced_artifacts])

        write_text_atomic(layout.slot_root(slot_id) / "inbox" / "index.md", "\n".join(inbox_lines) + "\n")
        write_text_atomic(layout.slot_root(slot_id) / "outbox" / "index.md", "\n".join(outbox_lines) + "\n")


def _status_snapshot(layout: ProjectLayout, warnings: List[str], recovery: Dict[str, int]) -> Dict[str, Any]:
    project = load_project(layout)
    topology = load_topology(layout)
    slots = read_all_slots(layout)
    task_counts = _task_counts(layout)
    pending = [_approval_summary(approval) for approval in _pending_approvals(layout)]
    return {
        "project": _project_summary(project),
        "topology": _topology_summary(topology, slots),
        "active_work_count": task_counts["active"],
        "queued_work_count": task_counts["queued"],
        "blocked_work_count": task_counts["blocked"],
        "completed_work_count": task_counts["completed"],
        "pending_approvals": pending,
        "pending_approval_count": len(pending),
        "budget": _budget_summary(layout),
        "adapter_health": _adapter_health(layout),
        "recent_artifacts": recent_artifacts(layout),
        "warnings": list(warnings),
        "recovery": recovery,
    }


def _render_status_report(snapshot: Dict[str, Any]) -> str:
    lines = [
        "# Project Status",
        "",
        "## Project",
        f"- Project ID: {snapshot['project']['project_id']}",
        f"- Name: {snapshot['project']['name']}",
        f"- Status: {snapshot['project']['status']}",
        f"- Operating Mode: {snapshot['project']['operating_mode']}",
        "",
        "## Work",
        f"- Active: {snapshot['active_work_count']}",
        f"- Queued: {snapshot['queued_work_count']}",
        f"- Blocked: {snapshot['blocked_work_count']}",
        f"- Completed: {snapshot['completed_work_count']}",
        "",
        "## Pending Approvals",
    ]
    _render_list(
        lines,
        [
            f"{approval['approval_id']}: {approval['scope_type']} ({approval['reason']})"
            for approval in snapshot["pending_approvals"]
        ],
    )
    lines.extend(["", "## Budget", f"- Hard Stop Activation IDs: {', '.join(snapshot['budget']['hard_stop_activation_ids']) or 'none'}"])
    if snapshot["budget"]["hard_stop_activation_ids"]:
        lines.append("- Pending Stop Reason: budget_hard_limit")
    lines.append(f"- Consumed To Date: `{json.dumps(snapshot['budget']['consumed_to_date'], sort_keys=True)}`")
    lines.extend(["", "## Adapter Health"])
    _render_list(lines, [f"{name}: {value.get('status')} - {value.get('message')}" for name, value in snapshot["adapter_health"].items()])
    lines.extend(["", "## Recent Artifacts"])
    _render_list(lines, [f"{artifact['artifact_id']}: {artifact['path']}" for artifact in snapshot["recent_artifacts"]])
    lines.extend(["", "## Warnings"])
    _render_list(lines, snapshot["warnings"])
    lines.extend(["", "## Recovery", f"- {json.dumps(snapshot['recovery'], sort_keys=True)}"])
    return "\n".join(lines) + "\n"


def _render_topology_report(layout: ProjectLayout, scope_type: str, scope_id: Optional[str]) -> str:
    topology = load_topology(layout)
    slots = read_all_slots(layout)
    lines = [
        "# Team Topology",
        "",
        f"- Scope: {scope_type}",
        f"- Scope ID: {scope_id or 'project'}",
        f"- Generation: {topology.generation}",
        "",
        "## Slots",
    ]
    for slot_id in topology.active_slot_ids + topology.retired_slot_ids:
        if slot_id not in slots:
            continue
        slot = slots[slot_id]
        lines.append(f"- {slot.slot_id}: {slot.role.value}, {slot.status.value}, parent={slot.parent_slot_id}")
    if len(lines) == 7:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _render_pending_approvals_report(layout: ProjectLayout, scope_type: str, scope_id: Optional[str]) -> str:
    lines = [
        "# Pending Approvals",
        "",
        f"- Scope: {scope_type}",
        f"- Scope ID: {scope_id or 'project'}",
        "",
        "## Approvals",
    ]
    _render_list(
        lines,
        [
            f"{approval.get('approval_id')}: {approval.get('scope_type')} for {approval.get('scope_id')} - {approval.get('reason')}"
            for approval in _pending_approvals(layout)
        ],
    )
    return "\n".join(lines) + "\n"


def _render_next_steps_report(layout: ProjectLayout, scope_type: str, scope_id: Optional[str]) -> str:
    blocked = [task for task in _tasks(layout) if task.get("status") == "blocked"]
    queued = [task for task in _tasks(layout) if task.get("status") == "queued"]
    lines = [
        "# Next Steps",
        "",
        f"- Scope: {scope_type}",
        f"- Scope ID: {scope_id or 'project'}",
        "",
        "## Blocked Work",
    ]
    _render_list(lines, [f"{task.get('task_id')}: {task.get('block_reason')} - {task.get('title')}" for task in blocked])
    lines.extend(["", "## Queued Work"])
    _render_list(lines, [f"{task.get('task_id')}: {task.get('title')}" for task in queued])
    lines.extend(["", "## Pending Approvals"])
    _render_list(lines, [f"{approval.get('approval_id')}: {approval.get('scope_type')}" for approval in _pending_approvals(layout)])
    return "\n".join(lines) + "\n"


def _render_unimplemented_stage_report(report_type: str, scope_type: str, scope_id: Optional[str]) -> tuple[str, List[str]]:
    title = "Literature Review" if report_type == "literature_review" else "Experiment Summary"
    return (
        "\n".join(
            [
                f"# {title}",
                "",
                f"- Scope: {scope_type}",
                f"- Scope ID: {scope_id or 'project'}",
                "- Indexed Source Count: 0",
                "",
                "## Notes",
                f"- {title} sources are not available until the later roadmap stage is implemented.",
            ]
        )
        + "\n",
        [f"{title} sources unavailable"],
    )


def _project_wiki_artifacts(layout: ProjectLayout) -> List[Dict[str, Any]]:
    knowledge_state_path = layout.state_dir / "knowledge" / "project.json"
    if not knowledge_state_path.exists():
        return []
    current_paths = set((read_json(knowledge_state_path).get("compiled_artifact_ids_by_output_path") or {}).keys())
    by_path: Dict[str, Dict[str, Any]] = {}
    for artifact in list_artifacts(layout):
        path = artifact.get("path")
        if (
            isinstance(path, str)
            and path.startswith("shared/wiki/")
            and path in current_paths
            and artifact.get("visibility") == ArtifactVisibility.PROJECT_SHARED.value
        ):
            by_path[path] = artifact
    return [by_path[path] for path in sorted(by_path)]


def _render_literature_review_report(layout: ProjectLayout, scope_type: str, scope_id: Optional[str]) -> tuple[str, List[str]]:
    artifacts = _project_wiki_artifacts(layout)
    warnings: List[str] = []
    lines = [
        "# Literature Review",
        "",
        f"- Scope: {scope_type}",
        f"- Scope ID: {scope_id or 'project'}",
        f"- Indexed Source Count: {len(artifacts)}",
        "",
        "## Knowledge Sources",
    ]
    _render_list(lines, [f"{artifact.get('path')} ({artifact.get('artifact_id')})" for artifact in artifacts])
    lines.extend(["", "## Source Notes"])
    if not artifacts:
        warnings.append("Knowledge sources unavailable")
        lines.append("- No synced project knowledge is available under `shared/wiki/`.")
    for artifact in artifacts[:10]:
        relative_path = artifact.get("path")
        if not isinstance(relative_path, str):
            continue
        path = layout.root / relative_path
        if not path.exists():
            warnings.append(f"Knowledge artifact missing on disk: {relative_path}")
            continue
        excerpt = path.read_text(encoding="utf-8")[:600].strip().replace("\n", " ")
        lines.append(f"- {relative_path}: {excerpt or 'empty'}")
    return "\n".join(lines) + "\n", warnings


def _latest_artifact_for_path(layout: ProjectLayout, relative_path: str) -> Optional[Dict[str, Any]]:
    for artifact in reversed(list_artifacts(layout)):
        if artifact.get("path") == relative_path:
            return artifact
    return None


def _render_final_package(layout: ProjectLayout) -> tuple[str, List[str]]:
    project = load_project(layout)
    warnings: List[str] = []
    references = [
        ("Status", "shared/reports/status-latest.md"),
        ("Topology", "shared/reports/topology-latest.md"),
        ("Pending Approvals", "shared/reports/pending-approvals-latest.md"),
        ("Next Steps", "shared/reports/next-steps-latest.md"),
        ("Literature Review", "shared/reports/literature-review-latest.md"),
        ("Experiment Summary", "shared/reports/experiment-summary-latest.md"),
        ("Graph Report", "shared/graph/graph-report-latest.md"),
        ("Graph Export", "shared/graph/graph-export-latest.json"),
    ]
    lines = [
        "# Final Package",
        "",
        "## Project Identity",
        f"- Project ID: {project.project_id}",
        f"- Name: {project.name}",
        f"- Status: {project.status.value}",
        f"- Schema Version: {project.schema_version}",
        "",
        "## Report References",
    ]
    for title, path in references:
        artifact = _latest_artifact_for_path(layout, path)
        if artifact is None:
            warnings.append(f"{title} report unavailable")
            lines.append(f"- {title}: unavailable ({path})")
        else:
            lines.append(f"- {title}: {path} ({artifact.get('artifact_id')})")
    project_artifacts = [
        artifact
        for artifact in reversed(list_artifacts(layout))
        if artifact.get("visibility") == ArtifactVisibility.PROJECT_SHARED.value
    ][:20]
    lines.extend(["", "## Recent Project-Shared Artifacts"])
    _render_list(lines, [f"{artifact.get('artifact_id')}: {artifact.get('path')}" for artifact in project_artifacts])
    lines.extend(["", "## Package Warnings"])
    _render_list(lines, warnings)
    return "\n".join(lines) + "\n", warnings


def _validate_report_request(report_type: Any, scope_type: Any, scope_id: Optional[str]) -> tuple[str, str, Optional[str]]:
    if report_type not in REPORT_TYPES:
        raise CommandError("invalid_report_type", f"Unsupported report_type: {report_type}", report_type=report_type)
    if scope_type not in SCOPE_TYPES:
        raise CommandError("invalid_scope_type", f"Unsupported scope_type: {scope_type}", scope_type=scope_type)
    if report_type == "final_package" and scope_type != "project":
        raise CommandError("invalid_report_scope", "final_package supports project scope only", report_type=report_type, scope_type=scope_type)
    if scope_type != "project" and (not isinstance(scope_id, str) or not scope_id.strip()):
        raise CommandError("invalid_payload", "scope_id is required for non-project report scope", field="scope_id")
    if report_type == "experiment_summary" and scope_type not in {"project", "slot", "task", "experiment_run"}:
        raise CommandError("invalid_report_scope", "experiment_summary does not support this scope", report_type=report_type, scope_type=scope_type)
    if report_type == "literature_review" and scope_type == "task":
        raise CommandError("invalid_report_scope", "literature_review does not support task scope", report_type=report_type, scope_type=scope_type)
    return str(report_type), str(scope_type), scope_id.strip() if isinstance(scope_id, str) and scope_id.strip() else None


def _write_report(layout: ProjectLayout, report_type: str, content: str, source_artifact_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    timestamp = now_utc()
    slug = _report_slug(report_type)
    timestamped_path = f"shared/reports/{slug}-{_safe_timestamp(timestamp)}.md"
    latest_path = f"shared/reports/{slug}-latest.md"
    write_text_atomic(layout.root / timestamped_path, content)
    write_text_atomic(layout.root / latest_path, content)
    artifact = index_artifact(
        layout,
        path=latest_path,
        artifact_type=f"{report_type}_report",
        visibility=ArtifactVisibility.PROJECT_SHARED,
        producing_slot_id="supervisor",
        source_artifact_ids=source_artifact_ids or [],
        created_at=timestamp,
    )
    return {"timestamped_report_path": timestamped_path, "report_path": latest_path, "artifact": artifact_summary(artifact)}


def _generate_report_locked(
    layout: ProjectLayout,
    *,
    report_type: Any,
    scope_type: Any,
    scope_id: Optional[str],
    allow_pending_approval: bool,
) -> Dict[str, Any]:
    report_type, scope_type, scope_id = _validate_report_request(report_type, scope_type, scope_id)
    if report_type == "final_package" and allow_pending_approval:
        policy = read_json(layout.state_dir / "policies" / "approvals.json")
        final_package_policy = policy.get("final_package", "user")
        if final_package_policy != "immediate":
            from research_agent_team.application.approval_service import approval_summary, create_approval

            approver_type = ApprovalApproverType.SUPERVISOR if final_package_policy == "supervisor" else ApprovalApproverType.USER
            approval = create_approval(
                layout,
                scope_type=ApprovalScopeType.GENERATE_REPORT,
                scope_id="final_package",
                requested_by_slot_id="supervisor",
                approver_type=approver_type,
                reason="Final package generation requires approval.",
                request_payload={"report_type": report_type, "scope_type": scope_type, "scope_id": scope_id},
                slot_id="supervisor",
            )
            rebuild_slot_views(layout)
            return {
                "generated": False,
                "report_type": report_type,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "report_path": None,
                "timestamped_report_path": None,
                "artifact": None,
                "pending_approval": approval_summary(approval),
                "warnings": [],
            }

    warnings: List[str] = []
    if report_type == "status":
        content = _render_status_report(_status_snapshot(layout, [], {"recovered_activation_count": 0, "requeued_task_count": 0, "blocked_task_count": 0}))
    elif report_type == "topology":
        content = _render_topology_report(layout, scope_type, scope_id)
    elif report_type == "pending_approvals":
        content = _render_pending_approvals_report(layout, scope_type, scope_id)
    elif report_type == "next_steps":
        content = _render_next_steps_report(layout, scope_type, scope_id)
    elif report_type == "literature_review":
        content, warnings = _render_literature_review_report(layout, scope_type, scope_id)
    elif report_type == "experiment_summary":
        content, warnings = _render_unimplemented_stage_report(report_type, scope_type, scope_id)
    else:
        content, warnings = _render_final_package(layout)

    written = _write_report(layout, report_type, content)
    rebuild_slot_views(layout)
    project = load_project(layout)
    emit_event(layout, project.project_id, "report.generated", {"report_type": report_type, "path": written["report_path"]}, slot_id="supervisor")
    return {
        "generated": True,
        "report_type": report_type,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "pending_approval": None,
        "warnings": warnings,
        **written,
    }


def request_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_payload(payload)
    with project_lock(layout.lock_path):
        from research_agent_team.application.recovery_service import recover_stale_activations_locked

        project = load_project(layout)
        topology = load_topology(layout)
        warnings = ensure_support_surfaces(layout, topology.active_slot_ids + topology.retired_slot_ids)
        recovery = recover_stale_activations_locked(layout)
        rebuild_slot_views(layout)
        snapshot = _status_snapshot(layout, warnings, recovery)
        content = _render_status_report(snapshot)
        written = _write_report(layout, "status", content)
        emit_event(layout, project.project_id, "status.requested", {"path": written["report_path"]}, slot_id="supervisor")
        return {**snapshot, **written}


def generate_report(payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_payload(payload)
    report_type = _require_string(payload, "report_type")
    scope_type = payload.get("scope_type", "project")
    if not isinstance(scope_type, str) or not scope_type.strip():
        raise CommandError("invalid_payload", "scope_type must be a string", field="scope_type")
    scope_id = payload.get("scope_id")
    if scope_id is not None and not isinstance(scope_id, str):
        raise CommandError("invalid_payload", "scope_id must be a string", field="scope_id")
    with project_lock(layout.lock_path):
        return _generate_report_locked(
            layout,
            report_type=report_type,
            scope_type=scope_type,
            scope_id=scope_id,
            allow_pending_approval=True,
        )
