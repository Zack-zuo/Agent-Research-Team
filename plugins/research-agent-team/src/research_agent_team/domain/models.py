from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from research_agent_team.domain.enums import ActivationStatus, OperatingMode, ProjectStatus, SlotRole, SlotStatus, TaskStatus


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def _coerce_enum(enum_type: Any, value: Any) -> Any:
    if isinstance(value, enum_type):
        return value
    return enum_type(value)


def _string_list(values: Optional[List[str]]) -> List[str]:
    return list(values or [])


@dataclass
class ResearchProject:
    project_id: str
    name: str
    schema_version: str
    status: ProjectStatus
    operating_mode: OperatingMode
    root_path: str
    created_at: str
    updated_at: str
    supervisor_slot_id: str
    policy_refs: Dict[str, str] = field(default_factory=dict)
    adapter_config_ref: str = "state/adapters/config.json"
    current_topology_ref: str = "state/topology/current.json"

    def __post_init__(self) -> None:
        self.status = _coerce_enum(ProjectStatus, self.status)
        self.operating_mode = _coerce_enum(OperatingMode, self.operating_mode)
        if not self.project_id.strip():
            raise ValueError("project_id is required")
        if not self.name.strip():
            raise ValueError("project name is required")
        if self.supervisor_slot_id != "supervisor":
            raise ValueError("supervisor_slot_id must be supervisor in v1")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "schema_version": self.schema_version,
            "status": self.status.value,
            "operating_mode": self.operating_mode.value,
            "root_path": self.root_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "supervisor_slot_id": self.supervisor_slot_id,
            "policy_refs": dict(self.policy_refs),
            "adapter_config_ref": self.adapter_config_ref,
            "current_topology_ref": self.current_topology_ref,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ResearchProject":
        return cls(**payload)


@dataclass
class AgentSlot:
    slot_id: str
    role: SlotRole
    parent_slot_id: Optional[str]
    status: SlotStatus
    workspace_root: str
    kb_root: str
    inbox_root: str
    outbox_root: str
    created_at: str = ""
    updated_at: str = ""
    retired_at: Optional[str] = None
    current_activation_id: Optional[str] = None
    latest_checkpoint_id: Optional[str] = None
    active_task_ids: List[str] = field(default_factory=list)
    queued_task_ids: List[str] = field(default_factory=list)
    completed_task_ids: List[str] = field(default_factory=list)
    descendant_slot_ids: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.role = _coerce_enum(SlotRole, self.role)
        self.status = _coerce_enum(SlotStatus, self.status)
        self.active_task_ids = _string_list(self.active_task_ids)
        self.queued_task_ids = _string_list(self.queued_task_ids)
        self.completed_task_ids = _string_list(self.completed_task_ids)
        self.descendant_slot_ids = _string_list(self.descendant_slot_ids)

        if not self.slot_id.strip():
            raise ValueError("slot_id is required")
        if self.role == SlotRole.SUPERVISOR and self.parent_slot_id is not None:
            raise ValueError("supervisor slot cannot have a parent")
        if self.role != SlotRole.SUPERVISOR and not self.parent_slot_id:
            raise ValueError("non-supervisor slots require a parent_slot_id")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "role": self.role.value,
            "parent_slot_id": self.parent_slot_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "retired_at": self.retired_at,
            "workspace_root": self.workspace_root,
            "kb_root": self.kb_root,
            "inbox_root": self.inbox_root,
            "outbox_root": self.outbox_root,
            "current_activation_id": self.current_activation_id,
            "latest_checkpoint_id": self.latest_checkpoint_id,
            "active_task_ids": list(self.active_task_ids),
            "queued_task_ids": list(self.queued_task_ids),
            "completed_task_ids": list(self.completed_task_ids),
            "descendant_slot_ids": list(self.descendant_slot_ids),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AgentSlot":
        return cls(**payload)


@dataclass
class TeamTopology:
    project_id: str
    generation: int
    supervisor_slot_id: str
    parent_by_slot_id: Dict[str, Optional[str]]
    children_by_slot_id: Dict[str, List[str]]
    active_slot_ids: List[str]
    retired_slot_ids: List[str]
    updated_at: str

    def __post_init__(self) -> None:
        if self.generation < 0:
            raise ValueError("topology generation cannot be negative")
        if self.supervisor_slot_id not in self.parent_by_slot_id:
            raise ValueError("topology must include supervisor parent entry")
        self.children_by_slot_id = {key: list(value) for key, value in self.children_by_slot_id.items()}
        self.active_slot_ids = _string_list(self.active_slot_ids)
        self.retired_slot_ids = _string_list(self.retired_slot_ids)

    def to_dict(self) -> Dict[str, Any]:
        slots = sorted(set(self.active_slot_ids + self.retired_slot_ids))
        return {
            "project_id": self.project_id,
            "generation": self.generation,
            "supervisor_slot_id": self.supervisor_slot_id,
            "root_slot_id": self.supervisor_slot_id,
            "parent_by_slot_id": dict(self.parent_by_slot_id),
            "children_by_slot_id": {key: list(value) for key, value in self.children_by_slot_id.items()},
            "active_slot_ids": list(self.active_slot_ids),
            "retired_slot_ids": list(self.retired_slot_ids),
            "slots": slots,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "TeamTopology":
        return cls(
            project_id=payload["project_id"],
            generation=payload["generation"],
            supervisor_slot_id=payload.get("supervisor_slot_id", payload.get("root_slot_id", "supervisor")),
            parent_by_slot_id=payload["parent_by_slot_id"],
            children_by_slot_id=payload["children_by_slot_id"],
            active_slot_ids=payload.get("active_slot_ids", payload.get("slots", [])),
            retired_slot_ids=payload.get("retired_slot_ids", []),
            updated_at=payload["updated_at"],
        )


@dataclass
class Task:
    task_id: str
    project_id: str
    requester_slot_id: str
    owner_slot_id: str
    status: TaskStatus
    title: str
    description: str
    success_criteria: List[str]
    input_artifact_ids: List[str] = field(default_factory=list)
    input_path_roots: List[str] = field(default_factory=list)
    expected_output_types: List[str] = field(default_factory=list)
    budget_envelope: Dict[str, Any] = field(default_factory=dict)
    review_requirement: str = "none"
    created_at: str = ""
    updated_at: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    current_activation_id: Optional[str] = None
    latest_checkpoint_id: Optional[str] = None
    block_reason: Optional[str] = None
    approval_policy_ref: Optional[str] = None
    current_approval_id: Optional[str] = None

    def __post_init__(self) -> None:
        self.status = _coerce_enum(TaskStatus, self.status)
        self.success_criteria = _string_list(self.success_criteria)
        self.input_artifact_ids = _string_list(self.input_artifact_ids)
        self.input_path_roots = _string_list(self.input_path_roots)
        self.expected_output_types = _string_list(self.expected_output_types)
        if not self.task_id.strip():
            raise ValueError("task_id is required")
        if not self.title.strip():
            raise ValueError("task title is required")
        if self.review_requirement not in {"none", "experiment_review"}:
            raise ValueError("unsupported review_requirement")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "requester_slot_id": self.requester_slot_id,
            "owner_slot_id": self.owner_slot_id,
            "status": self.status.value,
            "title": self.title,
            "description": self.description,
            "success_criteria": list(self.success_criteria),
            "input_artifact_ids": list(self.input_artifact_ids),
            "input_path_roots": list(self.input_path_roots),
            "expected_output_types": list(self.expected_output_types),
            "budget_envelope": dict(self.budget_envelope),
            "review_requirement": self.review_requirement,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "current_activation_id": self.current_activation_id,
            "latest_checkpoint_id": self.latest_checkpoint_id,
            "block_reason": self.block_reason,
            "approval_policy_ref": self.approval_policy_ref,
            "current_approval_id": self.current_approval_id,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Task":
        return cls(**payload)


@dataclass
class AgentActivation:
    activation_id: str
    slot_id: str
    task_id: str
    bundle_id: str
    status: ActivationStatus
    lease_acquired_at: str
    lease_heartbeat_at: str
    lease_timeout_seconds: int
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    runtime_pid: Optional[int] = None
    input_artifact_ids: List[str] = field(default_factory=list)
    output_artifact_ids: List[str] = field(default_factory=list)
    checkpoint_before_id: Optional[str] = None
    checkpoint_after_id: Optional[str] = None
    failure_summary: Optional[str] = None
    consumed_budget: Dict[str, float] = field(default_factory=dict)
    pending_stop_reason: Optional[str] = None

    def __post_init__(self) -> None:
        self.status = _coerce_enum(ActivationStatus, self.status)
        self.input_artifact_ids = _string_list(self.input_artifact_ids)
        self.output_artifact_ids = _string_list(self.output_artifact_ids)
        if not self.activation_id.strip():
            raise ValueError("activation_id is required")
        if self.lease_timeout_seconds <= 0:
            raise ValueError("lease_timeout_seconds must be positive")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "activation_id": self.activation_id,
            "slot_id": self.slot_id,
            "task_id": self.task_id,
            "bundle_id": self.bundle_id,
            "status": self.status.value,
            "lease_acquired_at": self.lease_acquired_at,
            "lease_heartbeat_at": self.lease_heartbeat_at,
            "lease_timeout_seconds": self.lease_timeout_seconds,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "runtime_pid": self.runtime_pid,
            "input_artifact_ids": list(self.input_artifact_ids),
            "output_artifact_ids": list(self.output_artifact_ids),
            "checkpoint_before_id": self.checkpoint_before_id,
            "checkpoint_after_id": self.checkpoint_after_id,
            "failure_summary": self.failure_summary,
            "consumed_budget": dict(self.consumed_budget),
            "pending_stop_reason": self.pending_stop_reason,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AgentActivation":
        return cls(**payload)


@dataclass
class SlotCheckpoint:
    checkpoint_id: str
    slot_id: str
    task_id: str
    activation_id: str
    created_at: str
    summary: str
    resume_instructions: str
    output_artifact_ids: List[str] = field(default_factory=list)
    materialized_path: str = ""

    def __post_init__(self) -> None:
        self.output_artifact_ids = _string_list(self.output_artifact_ids)
        if not self.checkpoint_id.strip():
            raise ValueError("checkpoint_id is required")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "slot_id": self.slot_id,
            "task_id": self.task_id,
            "activation_id": self.activation_id,
            "created_at": self.created_at,
            "summary": self.summary,
            "resume_instructions": self.resume_instructions,
            "output_artifact_ids": list(self.output_artifact_ids),
            "materialized_path": self.materialized_path,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SlotCheckpoint":
        return cls(**payload)


@dataclass
class TaskBundle:
    bundle_id: str
    task_id: str
    slot_id: str
    briefing_summary: str
    success_criteria: List[str]
    allowed_artifact_ids: List[str]
    allowed_path_roots: List[str]
    expected_output_types: List[str]
    effective_budget_envelope: Dict[str, Any]
    granted_permissions: List[str]
    review_gates: List[str]
    generated_at: str
    experiment_request_id: Optional[str] = None
    experiment_run_id: Optional[str] = None
    compare_run_ids: List[str] = field(default_factory=list)
    run_parameters: Dict[str, Any] = field(default_factory=dict)
    resume_checkpoint_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "task_id": self.task_id,
            "slot_id": self.slot_id,
            "briefing_summary": self.briefing_summary,
            "success_criteria": list(self.success_criteria),
            "allowed_artifact_ids": list(self.allowed_artifact_ids),
            "allowed_path_roots": list(self.allowed_path_roots),
            "expected_output_types": list(self.expected_output_types),
            "effective_budget_envelope": dict(self.effective_budget_envelope),
            "granted_permissions": list(self.granted_permissions),
            "review_gates": list(self.review_gates),
            "experiment_request_id": self.experiment_request_id,
            "experiment_run_id": self.experiment_run_id,
            "compare_run_ids": list(self.compare_run_ids),
            "run_parameters": dict(self.run_parameters),
            "resume_checkpoint_id": self.resume_checkpoint_id,
            "generated_at": self.generated_at,
        }


@dataclass
class ExperimentRequest:
    experiment_request_id: str
    project_id: str
    requester_slot_id: str
    executor_slot_id: str
    reviewer_slot_id: str
    task_id: str
    status: str
    title: str
    objective: str
    hypothesis: str
    method: str
    run_parameters: Dict[str, Any] = field(default_factory=dict)
    input_artifact_ids: List[str] = field(default_factory=list)
    input_path_roots: List[str] = field(default_factory=list)
    expected_output_types: List[str] = field(default_factory=list)
    compare_run_ids: List[str] = field(default_factory=list)
    budget_envelope: Dict[str, Any] = field(default_factory=dict)
    current_approval_id: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"awaiting_approval", "queued", "admitted", "cancelled"}:
            raise ValueError("unsupported experiment request status")
        self.input_artifact_ids = _string_list(self.input_artifact_ids)
        self.input_path_roots = _string_list(self.input_path_roots)
        self.expected_output_types = _string_list(self.expected_output_types)
        self.compare_run_ids = _string_list(self.compare_run_ids)
        if not self.experiment_request_id.strip():
            raise ValueError("experiment_request_id is required")
        if not self.title.strip():
            raise ValueError("experiment title is required")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experiment_request_id": self.experiment_request_id,
            "project_id": self.project_id,
            "requester_slot_id": self.requester_slot_id,
            "executor_slot_id": self.executor_slot_id,
            "reviewer_slot_id": self.reviewer_slot_id,
            "task_id": self.task_id,
            "status": self.status,
            "title": self.title,
            "objective": self.objective,
            "hypothesis": self.hypothesis,
            "method": self.method,
            "run_parameters": dict(self.run_parameters),
            "input_artifact_ids": list(self.input_artifact_ids),
            "input_path_roots": list(self.input_path_roots),
            "expected_output_types": list(self.expected_output_types),
            "compare_run_ids": list(self.compare_run_ids),
            "budget_envelope": dict(self.budget_envelope),
            "current_approval_id": self.current_approval_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ExperimentRequest":
        return cls(**payload)


@dataclass
class ExperimentRun:
    experiment_run_id: str
    experiment_request_id: str
    project_id: str
    task_id: str
    status: str
    run_root: str
    activation_id: Optional[str] = None
    adapter_type: Optional[str] = None
    prepared_manifest_path: Optional[str] = None
    published_artifact_ids: List[str] = field(default_factory=list)
    comparison_ids: List[str] = field(default_factory=list)
    failure_artifact_id: Optional[str] = None
    consumed_budget: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    started_at: Optional[str] = None
    published_at: Optional[str] = None
    reviewed_at: Optional[str] = None
    ended_at: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in {"prepared", "running", "awaiting_review", "reviewed", "failed", "interrupted", "cancelled"}:
            raise ValueError("unsupported experiment run status")
        self.published_artifact_ids = _string_list(self.published_artifact_ids)
        self.comparison_ids = _string_list(self.comparison_ids)
        if not self.experiment_run_id.strip():
            raise ValueError("experiment_run_id is required")
        if not self.run_root.strip():
            raise ValueError("experiment run_root is required")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experiment_run_id": self.experiment_run_id,
            "experiment_request_id": self.experiment_request_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "activation_id": self.activation_id,
            "status": self.status,
            "adapter_type": self.adapter_type,
            "prepared_manifest_path": self.prepared_manifest_path,
            "run_root": self.run_root,
            "published_artifact_ids": list(self.published_artifact_ids),
            "comparison_ids": list(self.comparison_ids),
            "failure_artifact_id": self.failure_artifact_id,
            "consumed_budget": dict(self.consumed_budget),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "published_at": self.published_at,
            "reviewed_at": self.reviewed_at,
            "ended_at": self.ended_at,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ExperimentRun":
        return cls(**payload)


@dataclass
class ExperimentComparison:
    comparison_id: str
    primary_run_id: str
    compared_run_ids: List[str]
    status: str
    output_artifact_ids: List[str] = field(default_factory=list)
    created_at: str = ""
    completed_at: Optional[str] = None
    failure_summary: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in {"pending", "completed", "failed"}:
            raise ValueError("unsupported experiment comparison status")
        self.compared_run_ids = _string_list(self.compared_run_ids)
        self.output_artifact_ids = _string_list(self.output_artifact_ids)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "comparison_id": self.comparison_id,
            "primary_run_id": self.primary_run_id,
            "compared_run_ids": list(self.compared_run_ids),
            "status": self.status,
            "output_artifact_ids": list(self.output_artifact_ids),
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "failure_summary": self.failure_summary,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ExperimentComparison":
        return cls(**payload)


@dataclass
class ExperimentReview:
    review_id: str
    experiment_run_id: str
    reviewer_slot_id: str
    outcome: str
    decision_summary: str
    review_artifact_id: str
    comparison_ids: List[str] = field(default_factory=list)
    follow_up_task_id: Optional[str] = None
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in {"accepted", "needs_follow_up"}:
            raise ValueError("unsupported experiment review outcome")
        self.comparison_ids = _string_list(self.comparison_ids)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "review_id": self.review_id,
            "experiment_run_id": self.experiment_run_id,
            "reviewer_slot_id": self.reviewer_slot_id,
            "outcome": self.outcome,
            "decision_summary": self.decision_summary,
            "review_artifact_id": self.review_artifact_id,
            "comparison_ids": list(self.comparison_ids),
            "follow_up_task_id": self.follow_up_task_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ExperimentReview":
        return cls(**payload)


@dataclass
class Event:
    event_id: str
    event_type: str
    created_at: str
    project_id: Optional[str] = None
    slot_id: Optional[str] = None
    task_id: Optional[str] = None
    activation_id: Optional[str] = None
    approval_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "created_at": self.created_at,
            "project_id": self.project_id,
            "slot_id": self.slot_id,
            "task_id": self.task_id,
            "activation_id": self.activation_id,
            "approval_id": self.approval_id,
            "payload": dict(self.payload),
        }
