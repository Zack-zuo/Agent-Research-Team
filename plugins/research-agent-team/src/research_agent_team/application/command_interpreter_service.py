from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional

from research_agent_team.contracts.interpretation import CommandInterpretation, InterpretationContext
from research_agent_team.storage import ProjectLayout, read_json


SLOT_ID_RE = re.compile(r"\b(?:supervisor|senior-\d{2}|junior-\d{2})\b")
PATH_RE = re.compile(r"\b(?:shared|agents|experiments)/(?:[A-Za-z0-9._/-]+)?")
APPROVAL_RE = re.compile(r"\bapproval-[A-Za-z0-9-]+\b")
EXPERIMENT_RUN_RE = re.compile(r"\bexperiment-run-[A-Za-z0-9-]+\b")


@dataclass(frozen=True)
class CommandSpec:
    required: tuple[str, ...]
    types: Dict[str, str] = field(default_factory=dict)
    enums: Dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass
class ProjectContext:
    root_path: Optional[str]
    project: Dict[str, Any] = field(default_factory=dict)
    slots: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    active_slot_ids: List[str] = field(default_factory=list)
    pending_approvals: List[Dict[str, Any]] = field(default_factory=list)
    recent_tasks: List[Dict[str, Any]] = field(default_factory=list)
    latest_checkpoint_id: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    invalid_root_path: bool = False

    def active_slots_by_role(self, role: str) -> List[str]:
        return [
            slot_id
            for slot_id in self.active_slot_ids
            if self.slots.get(slot_id, {}).get("role") == role and self.slots.get(slot_id, {}).get("status", "active") == "active"
        ]


COMMAND_SPECS: Dict[str, CommandSpec] = {
    "create_project": CommandSpec(required=("name", "root_path"), types={"name": "string", "root_path": "string"}),
    "open_project": CommandSpec(required=("root_path",), types={"root_path": "string"}),
    "switch_operating_mode": CommandSpec(
        required=("root_path", "operating_mode"),
        types={"root_path": "string", "operating_mode": "string"},
        enums={"operating_mode": ("autonomous", "semi_autonomous")},
    ),
    "pause_project": CommandSpec(required=("root_path",), types={"root_path": "string"}),
    "resume_project": CommandSpec(required=("root_path",), types={"root_path": "string"}),
    "show_team_topology": CommandSpec(required=("root_path",), types={"root_path": "string"}),
    "add_senior": CommandSpec(required=("root_path",), types={"root_path": "string"}),
    "add_junior": CommandSpec(required=("root_path", "parent_slot_id"), types={"root_path": "string", "parent_slot_id": "string"}),
    "retire_senior": CommandSpec(required=("root_path", "slot_id"), types={"root_path": "string", "slot_id": "string"}),
    "retire_junior": CommandSpec(required=("root_path", "slot_id"), types={"root_path": "string", "slot_id": "string"}),
    "assign_task": CommandSpec(
        required=("root_path", "requester_slot_id", "owner_slot_id", "title", "description"),
        types={
            "root_path": "string",
            "requester_slot_id": "string",
            "owner_slot_id": "string",
            "title": "string",
            "description": "string",
            "success_criteria": "list",
            "input_artifact_ids": "list",
            "input_path_roots": "list",
            "expected_output_types": "list",
            "budget_override": "object",
        },
    ),
    "approve_checkpoint": CommandSpec(
        required=("root_path", "approval_id"),
        types={"root_path": "string", "approval_id": "string", "decision_summary": "string"},
    ),
    "reject_checkpoint": CommandSpec(
        required=("root_path", "approval_id"),
        types={"root_path": "string", "approval_id": "string", "decision_summary": "string"},
    ),
    "request_status": CommandSpec(required=("root_path",), types={"root_path": "string"}),
    "generate_report": CommandSpec(
        required=("root_path", "report_type"),
        types={"root_path": "string", "report_type": "string", "scope_type": "string", "scope_id": "string"},
        enums={
            "report_type": (
                "status",
                "topology",
                "pending_approvals",
                "next_steps",
                "literature_review",
                "experiment_summary",
                "final_package",
            ),
            "scope_type": ("project", "slot", "task", "experiment_run"),
        },
    ),
    "sync_knowledge_base": CommandSpec(
        required=("root_path", "scope_type"),
        types={"root_path": "string", "scope_type": "string", "scope_id": "string", "mode": "string"},
        enums={"scope_type": ("project", "slot"), "mode": ("incremental", "full")},
    ),
    "rebuild_graph": CommandSpec(
        required=("root_path",),
        types={"root_path": "string", "mode": "string"},
        enums={"mode": ("incremental", "full")},
    ),
    "run_experiment": CommandSpec(
        required=("root_path", "requester_slot_id", "title", "objective", "method", "success_criteria"),
        types={
            "root_path": "string",
            "requester_slot_id": "string",
            "executor_slot_id": "string",
            "adapter_type": "string",
            "title": "string",
            "objective": "string",
            "hypothesis": "string",
            "method": "string",
            "success_criteria": "list",
            "input_artifact_ids": "list",
            "input_path_roots": "list",
            "expected_output_types": "list",
            "run_parameters": "object",
            "compare_run_ids": "list",
            "budget_override": "object",
        },
    ),
    "review_experiment": CommandSpec(
        required=("root_path", "experiment_run_id", "reviewer_slot_id", "outcome", "decision_summary"),
        types={
            "root_path": "string",
            "experiment_run_id": "string",
            "reviewer_slot_id": "string",
            "outcome": "string",
            "decision_summary": "string",
        },
        enums={"outcome": ("accepted", "needs_follow_up")},
    ),
}

READ_INTENTS = {"open_project", "request_status", "show_team_topology"}
CONSERVATIVE_CONFIRM_COMMANDS = {
    "switch_operating_mode",
    "pause_project",
    "resume_project",
    "add_senior",
    "add_junior",
    "retire_senior",
    "retire_junior",
    "assign_task",
    "approve_checkpoint",
    "reject_checkpoint",
    "run_experiment",
    "review_experiment",
}


def interpret_command(text: str, context: InterpretationContext | Dict[str, Any] | None = None) -> CommandInterpretation:
    ctx = InterpretationContext.from_any(context)
    raw_text = text if isinstance(text, str) else ""
    normalized = _normalize(raw_text)
    project_context = _load_project_context(ctx)

    if not normalized:
        return _finalize(
            CommandInterpretation(
                intent="unknown",
                confidence=0.0,
                command_name=None,
                missing_fields=["text"],
                warnings=["No natural-language request was provided."],
            ),
            ctx,
            project_context,
        )

    interpretation = _infer_interpretation(raw_text.strip(), normalized, ctx, project_context)
    return _finalize(interpretation, ctx, project_context)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _is_project_root(path: Path) -> bool:
    return (path / "project.yaml").exists() and (path / "state" / "project.json").exists()


def _resolve_root_path(ctx: InterpretationContext) -> Optional[str]:
    if ctx.root_path:
        return str(Path(ctx.root_path).expanduser().resolve())
    cwd = Path(ctx.cwd).expanduser().resolve() if ctx.cwd else Path.cwd().resolve()
    if _is_project_root(cwd):
        return str(cwd)
    return None


def _load_project_context(ctx: InterpretationContext) -> ProjectContext:
    root_path = _resolve_root_path(ctx)
    result = ProjectContext(root_path=root_path)
    context_slots = {
        slot["slot_id"]: slot
        for slot in ctx.available_slots
        if isinstance(slot.get("slot_id"), str)
    }
    context_pending_approvals = list(ctx.pending_approvals) or [{"approval_id": approval_id} for approval_id in ctx.pending_approval_ids]
    context_recent_tasks = list(ctx.recent_tasks) or [{"task_id": task_id} for task_id in ctx.recent_task_ids]
    if not root_path:
        result.slots = context_slots
        result.active_slot_ids = list(ctx.available_slot_ids) or [
            slot_id
            for slot_id, slot in context_slots.items()
            if slot.get("status", "active") == "active"
        ]
        result.latest_checkpoint_id = ctx.latest_checkpoint_id
        result.pending_approvals = context_pending_approvals
        result.recent_tasks = context_recent_tasks
        return result

    layout = ProjectLayout(Path(root_path))
    try:
        if not _is_project_root(layout.root):
            result.invalid_root_path = True
            result.warnings.append("root_path does not reference an existing ResearchAgentTeam project.")
        elif layout.project_state.exists():
            result.project = read_json(layout.project_state)
        if layout.topology_state.exists():
            topology = read_json(layout.topology_state)
            result.active_slot_ids = [slot_id for slot_id in topology.get("active_slot_ids", []) if isinstance(slot_id, str)]
        result.slots = {**context_slots, **_read_slots(layout)}
        if not result.active_slot_ids:
            result.active_slot_ids = [
                slot_id
                for slot_id, slot in result.slots.items()
                if slot.get("status") == "active"
            ]
        result.pending_approvals = _read_pending_approvals(layout) or context_pending_approvals
        result.recent_tasks = _read_recent_tasks(layout) or context_recent_tasks
        result.latest_checkpoint_id = ctx.latest_checkpoint_id or _latest_checkpoint_id(layout, result.active_slot_ids)
    except Exception as exc:
        result.warnings.append(f"Could not read project context: {exc}")
    return result


def _read_slots(layout: ProjectLayout) -> Dict[str, Dict[str, Any]]:
    slots: Dict[str, Dict[str, Any]] = {}
    slots_dir = layout.state_dir / "slots"
    if not slots_dir.exists():
        return slots
    for path in sorted(slots_dir.glob("*.json")):
        slot = read_json(path)
        slot_id = slot.get("slot_id")
        if isinstance(slot_id, str):
            slots[slot_id] = slot
    return slots


def _read_pending_approvals(layout: ProjectLayout) -> List[Dict[str, Any]]:
    approvals_dir = layout.state_dir / "approvals"
    if not approvals_dir.exists():
        return []
    approvals = []
    for path in sorted(approvals_dir.glob("*.json")):
        approval = read_json(path)
        if approval.get("status") == "pending":
            approvals.append(approval)
    return approvals


def _read_recent_tasks(layout: ProjectLayout, limit: int = 10) -> List[Dict[str, Any]]:
    tasks_dir = layout.state_dir / "tasks"
    if not tasks_dir.exists():
        return []
    tasks = [read_json(path) for path in sorted(tasks_dir.glob("*.json"))]
    tasks.sort(key=lambda task: str(task.get("updated_at") or task.get("created_at") or ""))
    return tasks[-limit:]


def _latest_checkpoint_id(layout: ProjectLayout, active_slot_ids: Iterable[str]) -> Optional[str]:
    latest: Optional[Dict[str, Any]] = None
    for slot_id in active_slot_ids:
        path = layout.slot_checkpoint_state_path(slot_id)
        if not path.exists():
            continue
        checkpoint = read_json(path)
        if latest is None or str(checkpoint.get("created_at", "")) > str(latest.get("created_at", "")):
            latest = checkpoint
    if latest and isinstance(latest.get("checkpoint_id"), str):
        return latest["checkpoint_id"]
    return None


def _infer_interpretation(
    raw_text: str,
    normalized: str,
    ctx: InterpretationContext,
    project_context: ProjectContext,
) -> CommandInterpretation:
    if _matches(normalized, ("open", "load", "return to", "reopen")) and "project" in normalized:
        return _base("open_project", 0.9, {"root_path": project_context.root_path})

    if "report" in normalized and _matches(normalized, ("generate", "write", "create", "produce")):
        return _generate_report(_report_type_from_text(normalized), project_context.root_path, 0.84)

    if _matches(normalized, ("show", "status", "progress", "current progress", "what is happening")):
        if "topology" in normalized or "team" in normalized:
            return _base("show_team_topology", 0.86, {"root_path": project_context.root_path})
        if "report" in normalized and "final" in normalized:
            return _generate_report("final_package", project_context.root_path, 0.82)
        if "literature" in normalized and "report" in normalized:
            return _generate_report("literature_review", project_context.root_path, 0.82)
        return _base("request_status", 0.88, {"root_path": project_context.root_path})

    if "pause" in normalized and "project" in normalized:
        return _base("pause_project", 0.91, {"root_path": project_context.root_path})

    if ("resume" in normalized or "continue" in normalized) and ("project" in normalized or "checkpoint" in normalized):
        interpretation = _base("resume_project", 0.9, {"root_path": project_context.root_path})
        if project_context.latest_checkpoint_id:
            interpretation.resolved_references.append(
                {"field": "latest_checkpoint_id", "value": project_context.latest_checkpoint_id, "source": "project_state"}
            )
        elif "checkpoint" in normalized:
            interpretation.warnings.append("No latest checkpoint was found in project state.")
        if "task" in normalized and project_context.recent_tasks:
            task_id = project_context.recent_tasks[-1].get("task_id")
            if isinstance(task_id, str):
                interpretation.resolved_references.append({"field": "recent_task_id", "value": task_id, "source": "recent_tasks"})
        return interpretation

    if "knowledge" in normalized and _matches(normalized, ("sync", "refresh", "update")):
        payload = {"root_path": project_context.root_path, "scope_type": "project", "mode": _mode_from_text(normalized)}
        slot_id = _explicit_slot_id(normalized)
        if slot_id:
            payload["scope_type"] = "slot"
            payload["scope_id"] = slot_id
        return _base("sync_knowledge_base", 0.88, payload)

    if "graph" in normalized and _matches(normalized, ("rebuild", "build", "refresh", "sync")):
        return _base("rebuild_graph", 0.88, {"root_path": project_context.root_path, "mode": _mode_from_text(normalized)})

    if "review" in normalized and "experiment" in normalized:
        return _review_experiment(normalized, ctx, project_context)

    if "experiment" in normalized and _matches(normalized, ("run", "start", "launch")):
        return _run_experiment(raw_text, normalized, ctx, project_context)

    if _matches(normalized, ("assign", "delegate", "ask")) and ("task" in normalized or SLOT_ID_RE.search(normalized)):
        return _assign_task(raw_text, normalized, ctx, project_context)

    if _matches(normalized, ("approve", "accept")) and ("approval" in normalized or "checkpoint" in normalized):
        approval_id = _approval_id(normalized, project_context)
        interpretation = _base(
            "approve_checkpoint",
            0.79,
            {
                "root_path": project_context.root_path,
                "approval_id": approval_id,
                "decision_summary": "Approved from natural-language request.",
            },
        )
        if approval_id:
            interpretation.resolved_references.append({"field": "approval_id", "value": approval_id, "source": "pending_approvals"})
        return interpretation

    if _matches(normalized, ("reject", "deny")) and ("approval" in normalized or "checkpoint" in normalized):
        approval_id = _approval_id(normalized, project_context)
        interpretation = _base(
            "reject_checkpoint",
            0.79,
            {
                "root_path": project_context.root_path,
                "approval_id": approval_id,
                "decision_summary": "Rejected from natural-language request.",
            },
        )
        if approval_id:
            interpretation.resolved_references.append({"field": "approval_id", "value": approval_id, "source": "pending_approvals"})
        return interpretation

    if "junior" in normalized and _matches(normalized, ("add", "create", "hire")):
        parent_slot_id = _explicit_slot_id(normalized)
        payload = {"root_path": project_context.root_path, "parent_slot_id": parent_slot_id}
        interpretation = _base("add_junior", 0.76, payload)
        if parent_slot_id is None:
            seniors = project_context.active_slots_by_role("senior_phd")
            if len(seniors) == 1:
                payload["parent_slot_id"] = seniors[0]
                interpretation.inferred_fields.append("parent_slot_id")
                interpretation.resolved_references.append({"field": "parent_slot_id", "value": seniors[0], "source": "active_senior_slot"})
            elif seniors:
                interpretation.ambiguous_references.append({"field": "parent_slot_id", "candidates": seniors, "source": "active_senior_slots"})
        return interpretation

    if "senior" in normalized and _matches(normalized, ("add", "create", "hire")):
        return _base("add_senior", 0.76, {"root_path": project_context.root_path})

    if "retire" in normalized and "senior" in normalized:
        return _base("retire_senior", 0.78, {"root_path": project_context.root_path, "slot_id": _explicit_slot_id(normalized)})

    if "retire" in normalized and "junior" in normalized:
        return _base("retire_junior", 0.78, {"root_path": project_context.root_path, "slot_id": _explicit_slot_id(normalized)})

    return CommandInterpretation(
        intent="unknown",
        confidence=0.2,
        command_name=None,
        warnings=["Could not map request to a known ResearchAgentTeam command."],
    )


def _base(command_name: str, confidence: float, payload: Dict[str, Any]) -> CommandInterpretation:
    return CommandInterpretation(intent=command_name, confidence=confidence, command_name=command_name, payload={k: v for k, v in payload.items() if v is not None})


def _matches(text: str, words: Iterable[str]) -> bool:
    return any(word in text for word in words)


def _generate_report(report_type: str, root_path: Optional[str], confidence: float) -> CommandInterpretation:
    return _base("generate_report", confidence, {"root_path": root_path, "report_type": report_type, "scope_type": "project"})


def _report_type_from_text(text: str) -> str:
    if "final" in text or "package" in text:
        return "final_package"
    if "literature" in text:
        return "literature_review"
    if "experiment" in text:
        return "experiment_summary"
    if "approval" in text:
        return "pending_approvals"
    if "next" in text:
        return "next_steps"
    if "topology" in text or "team" in text:
        return "topology"
    return "status"


def _mode_from_text(text: str) -> str:
    if any(token in text for token in ("full", "from scratch", "rebuild all", "resync all")):
        return "full"
    return "incremental"


def _explicit_slot_id(text: str) -> Optional[str]:
    match = SLOT_ID_RE.search(text)
    return match.group(0) if match else None


def _approval_id(text: str, project_context: ProjectContext) -> Optional[str]:
    match = APPROVAL_RE.search(text)
    if match:
        return match.group(0)
    if len(project_context.pending_approvals) == 1:
        approval_id = project_context.pending_approvals[0].get("approval_id")
        return approval_id if isinstance(approval_id, str) else None
    return None


def _requester_slot(ctx: InterpretationContext, project_context: ProjectContext) -> Optional[str]:
    if ctx.requester_slot_id:
        return ctx.requester_slot_id
    supervisor = project_context.project.get("supervisor_slot_id")
    return supervisor if isinstance(supervisor, str) and supervisor else "supervisor"


def _assign_task(raw_text: str, normalized: str, ctx: InterpretationContext, project_context: ProjectContext) -> CommandInterpretation:
    owner_slot_id = _explicit_slot_id(normalized)
    title = _task_title(normalized)
    payload: Dict[str, Any] = {
        "root_path": project_context.root_path,
        "requester_slot_id": _requester_slot(ctx, project_context),
        "owner_slot_id": owner_slot_id,
        "title": title,
        "description": _task_description(raw_text, title),
        "success_criteria": _success_criteria(title),
        "input_artifact_ids": [],
        "input_path_roots": _input_path_roots(raw_text),
        "expected_output_types": _expected_outputs(title),
    }
    interpretation = _base("assign_task", 0.86 if owner_slot_id else 0.68, payload)
    if not owner_slot_id:
        role = _mentioned_role(normalized)
        candidates = _candidate_slots_for_role(project_context, role)
        if candidates:
            interpretation.ambiguous_references.append({"field": "owner_slot_id", "candidates": candidates, "source": "active_slots"})
    return interpretation


def _task_title(text: str) -> str:
    if "literature review" in text:
        return "Literature review"
    if "replication" in text:
        return "Replication task"
    if "baseline" in text:
        return "Baseline task"
    return "Research task"


def _task_description(raw_text: str, title: str) -> str:
    return f"{title}. Inferred from natural-language request: {raw_text.strip()}"


def _success_criteria(title: str) -> List[str]:
    if title == "Literature review":
        return ["Produce a concise literature review", "List follow-up research questions"]
    if title == "Replication task":
        return ["Produce reproducible replication notes"]
    return ["Produce a concise result summary"]


def _expected_outputs(title: str) -> List[str]:
    if title in {"Literature review", "Replication task", "Research task", "Baseline task"}:
        return ["markdown"]
    return []


def _input_path_roots(text: str) -> List[str]:
    roots = []
    for match in PATH_RE.finditer(text):
        path = match.group(0).rstrip(".,;:")
        try:
            normalized = PurePosixPath(path)
        except ValueError:
            continue
        if normalized.is_absolute() or "." in normalized.parts or ".." in normalized.parts:
            continue
        roots.append(normalized.as_posix())
    return sorted(set(roots))


def _mentioned_role(text: str) -> Optional[str]:
    if "senior" in text:
        return "senior_phd"
    if "junior" in text:
        return "junior_phd"
    return None


def _candidate_slots_for_role(project_context: ProjectContext, role: Optional[str]) -> List[str]:
    if role:
        return project_context.active_slots_by_role(role)
    return [
        slot_id
        for slot_id in project_context.active_slot_ids
        if project_context.slots.get(slot_id, {}).get("role") in {"senior_phd", "junior_phd"}
    ]


def _run_experiment(raw_text: str, normalized: str, ctx: InterpretationContext, project_context: ProjectContext) -> CommandInterpretation:
    title = "Baseline experiment" if "baseline" in normalized else "Experiment"
    requester_slot_id = _experiment_requester(ctx, project_context)
    executor_slot_id = _experiment_executor(requester_slot_id, project_context)
    payload: Dict[str, Any] = {
        "root_path": project_context.root_path,
        "requester_slot_id": requester_slot_id,
        "executor_slot_id": executor_slot_id,
        "title": title,
        "objective": "Measure baseline behavior." if title == "Baseline experiment" else f"Run experiment inferred from: {raw_text.strip()}",
        "hypothesis": "The baseline produces reproducible evidence." if title == "Baseline experiment" else "",
        "method": "Run the configured local experiment adapter and publish evidence.",
        "success_criteria": ["Publish a reviewable evidence package"],
        "input_artifact_ids": [],
        "input_path_roots": _input_path_roots(raw_text),
        "expected_output_types": ["json", "markdown"],
        "run_parameters": {"variant": "baseline"} if title == "Baseline experiment" else {},
    }
    interpretation = _base("run_experiment", 0.84, payload)
    if requester_slot_id:
        interpretation.resolved_references.append({"field": "requester_slot_id", "value": requester_slot_id, "source": "active_senior_slot"})
    if executor_slot_id:
        interpretation.resolved_references.append({"field": "executor_slot_id", "value": executor_slot_id, "source": "active_junior_slot"})
    return interpretation


def _review_experiment(normalized: str, ctx: InterpretationContext, project_context: ProjectContext) -> CommandInterpretation:
    match = EXPERIMENT_RUN_RE.search(normalized)
    reviewer_slot_id = _experiment_requester(ctx, project_context) or _requester_slot(ctx, project_context)
    outcome = "needs_follow_up" if ("follow" in normalized or "needs follow" in normalized) else "accepted"
    decision_summary = (
        "Follow-up requested from natural-language request."
        if outcome == "needs_follow_up"
        else "Accepted from natural-language request."
    )
    return _base(
        "review_experiment",
        0.82 if match else 0.62,
        {
            "root_path": project_context.root_path,
            "experiment_run_id": match.group(0) if match else None,
            "reviewer_slot_id": reviewer_slot_id,
            "outcome": outcome,
            "decision_summary": decision_summary,
        },
    )


def _experiment_requester(ctx: InterpretationContext, project_context: ProjectContext) -> Optional[str]:
    if ctx.requester_slot_id and project_context.slots.get(ctx.requester_slot_id, {}).get("role") == "senior_phd":
        return ctx.requester_slot_id
    seniors = project_context.active_slots_by_role("senior_phd")
    if len(seniors) == 1:
        return seniors[0]
    return None


def _experiment_executor(requester_slot_id: Optional[str], project_context: ProjectContext) -> Optional[str]:
    if not requester_slot_id:
        return None
    requester = project_context.slots.get(requester_slot_id, {})
    descendants = requester.get("descendant_slot_ids") if isinstance(requester.get("descendant_slot_ids"), list) else []
    juniors = [
        slot_id
        for slot_id in descendants
        if project_context.slots.get(slot_id, {}).get("role") == "junior_phd"
        and project_context.slots.get(slot_id, {}).get("status", "active") == "active"
    ]
    if len(juniors) == 1:
        return juniors[0]
    return None


def _finalize(
    interpretation: CommandInterpretation,
    ctx: InterpretationContext,
    project_context: ProjectContext,
) -> CommandInterpretation:
    interpretation.warnings = [*project_context.warnings, *interpretation.warnings]
    _validate_payload(interpretation, project_context)
    _apply_confirmation_policy(interpretation, ctx)
    interpretation.ready_for_execution = bool(
        interpretation.command_name
        and not interpretation.missing_fields
        and not interpretation.ambiguous_references
        and not interpretation.validation_errors
    )
    if not interpretation.ready_for_execution and not interpretation.needs_confirmation:
        interpretation.needs_confirmation = True
        interpretation.confirmation_reason = interpretation.confirmation_reason or "Missing or invalid command fields require user confirmation."
    return interpretation


def _validate_payload(interpretation: CommandInterpretation, project_context: ProjectContext) -> None:
    if not interpretation.command_name:
        return
    spec = COMMAND_SPECS.get(interpretation.command_name)
    if not spec:
        interpretation.validation_errors.append(f"Unsupported command: {interpretation.command_name}")
        return

    if project_context.invalid_root_path and interpretation.command_name != "create_project":
        interpretation.validation_errors.append("root_path does not reference an existing ResearchAgentTeam project")

    for field_name in spec.required:
        value = interpretation.payload.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, list) and not value):
            if field_name not in interpretation.missing_fields:
                interpretation.missing_fields.append(field_name)

    for field_name, expected in spec.types.items():
        if field_name not in interpretation.payload or interpretation.payload[field_name] is None:
            continue
        value = interpretation.payload[field_name]
        if expected == "string" and not isinstance(value, str):
            interpretation.validation_errors.append(f"{field_name} must be a string")
        elif expected == "list" and not isinstance(value, list):
            interpretation.validation_errors.append(f"{field_name} must be a list")
        elif expected == "object" and not isinstance(value, dict):
            interpretation.validation_errors.append(f"{field_name} must be an object")

    for field_name, values in spec.enums.items():
        value = interpretation.payload.get(field_name)
        if value is not None and value not in values:
            interpretation.validation_errors.append(f"{field_name} must be one of: {', '.join(values)}")

    if interpretation.command_name == "sync_knowledge_base" and interpretation.payload.get("scope_type") == "slot":
        if not interpretation.payload.get("scope_id"):
            _append_missing(interpretation, "scope_id")

    _validate_slot_fields(interpretation, project_context)
    _validate_approval_field(interpretation, project_context)
    _validate_report_scope(interpretation)
    _validate_paths(interpretation)


def _append_missing(interpretation: CommandInterpretation, field_name: str) -> None:
    if field_name not in interpretation.missing_fields:
        interpretation.missing_fields.append(field_name)


def _validate_slot_fields(interpretation: CommandInterpretation, project_context: ProjectContext) -> None:
    if not project_context.slots:
        return
    for field_name in ("requester_slot_id", "owner_slot_id", "executor_slot_id", "reviewer_slot_id", "parent_slot_id", "slot_id", "scope_id"):
        if field_name == "scope_id" and interpretation.payload.get("scope_type") != "slot":
            continue
        slot_id = interpretation.payload.get(field_name)
        if not isinstance(slot_id, str) or not slot_id:
            continue
        slot = project_context.slots.get(slot_id)
        if slot is None or slot.get("status", "active") != "active":
            interpretation.validation_errors.append(f"{field_name} does not reference an active slot: {slot_id}")

    if interpretation.command_name == "run_experiment":
        requester = interpretation.payload.get("requester_slot_id")
        executor = interpretation.payload.get("executor_slot_id")
        if not executor:
            _append_missing(interpretation, "executor_slot_id")
            interpretation.warnings.append("No active junior executor could be resolved for the experiment.")
        if isinstance(requester, str) and project_context.slots.get(requester, {}).get("role") != "senior_phd":
            interpretation.validation_errors.append(f"requester_slot_id must reference an active senior slot: {requester}")
        if isinstance(executor, str) and project_context.slots.get(executor, {}).get("role") != "junior_phd":
            interpretation.validation_errors.append(f"executor_slot_id must reference an active junior slot: {executor}")


def _validate_approval_field(interpretation: CommandInterpretation, project_context: ProjectContext) -> None:
    if interpretation.command_name not in {"approve_checkpoint", "reject_checkpoint"}:
        return
    approval_id = interpretation.payload.get("approval_id")
    if not approval_id:
        if len(project_context.pending_approvals) > 1:
            interpretation.ambiguous_references.append(
                {
                    "field": "approval_id",
                    "candidates": [approval["approval_id"] for approval in project_context.pending_approvals if approval.get("approval_id")],
                    "source": "pending_approvals",
                }
            )
        return
    if project_context.pending_approvals and approval_id not in {approval.get("approval_id") for approval in project_context.pending_approvals}:
        interpretation.validation_errors.append(f"approval_id does not reference a pending approval: {approval_id}")


def _validate_report_scope(interpretation: CommandInterpretation) -> None:
    if interpretation.command_name != "generate_report":
        return
    report_type = interpretation.payload.get("report_type")
    scope_type = interpretation.payload.get("scope_type", "project")
    if report_type == "final_package" and scope_type != "project":
        interpretation.validation_errors.append("final_package supports project scope only")
    if scope_type != "project" and not interpretation.payload.get("scope_id"):
        _append_missing(interpretation, "scope_id")


def _validate_paths(interpretation: CommandInterpretation) -> None:
    for path in interpretation.payload.get("input_path_roots") or []:
        if not isinstance(path, str):
            interpretation.validation_errors.append("input_path_roots must contain strings")
            continue
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or "." in parsed.parts or ".." in parsed.parts:
            interpretation.validation_errors.append(f"input_path_roots must be project-relative and non-escaping: {path}")


def _apply_confirmation_policy(interpretation: CommandInterpretation, ctx: InterpretationContext) -> None:
    if interpretation.command_name is None:
        interpretation.needs_confirmation = True
        interpretation.confirmation_reason = "The request could not be mapped to a command."
        return
    if interpretation.missing_fields or interpretation.ambiguous_references or interpretation.validation_errors:
        interpretation.needs_confirmation = True
        interpretation.confirmation_reason = "The command plan has missing, ambiguous, or invalid fields."
        return
    if ctx.confirmation_mode == "aggressive":
        interpretation.needs_confirmation = False
        interpretation.confirmation_reason = None
        return
    if interpretation.command_name in CONSERVATIVE_CONFIRM_COMMANDS:
        interpretation.needs_confirmation = True
        interpretation.confirmation_reason = "Conservative mode requires confirmation before this state-changing command."
        return
    if interpretation.command_name in {"sync_knowledge_base", "rebuild_graph"} and interpretation.payload.get("mode") == "full":
        interpretation.needs_confirmation = True
        interpretation.confirmation_reason = "Full knowledge or graph rebuilds can rewrite derived outputs."
        return
    if interpretation.command_name == "generate_report" and interpretation.payload.get("report_type") == "final_package":
        interpretation.needs_confirmation = True
        interpretation.confirmation_reason = "Final package generation may require approval and should be confirmed."
        return
    interpretation.needs_confirmation = False
    interpretation.confirmation_reason = None
