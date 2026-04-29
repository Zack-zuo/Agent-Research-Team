from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from research_agent_team.domain.enums import OperatingMode, ProjectStatus, SlotRole, SlotStatus


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
