from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _string(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


@dataclass
class InterpretationContext:
    root_path: Optional[str] = None
    requester_slot_id: Optional[str] = None
    confirmation_mode: str = "conservative"
    cwd: Optional[str] = None
    available_slots: List[Dict[str, Any]] = field(default_factory=list)
    available_slot_ids: List[str] = field(default_factory=list)
    pending_approvals: List[Dict[str, Any]] = field(default_factory=list)
    pending_approval_ids: List[str] = field(default_factory=list)
    latest_checkpoint_id: Optional[str] = None
    recent_tasks: List[Dict[str, Any]] = field(default_factory=list)
    recent_task_ids: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.confirmation_mode not in {"conservative", "aggressive"}:
            self.confirmation_mode = "conservative"

    @classmethod
    def from_any(cls, value: Any = None) -> "InterpretationContext":
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise TypeError("interpretation context must be a mapping")

        current_project = value.get("current_project")
        current_project = current_project if isinstance(current_project, dict) else {}
        root_path = (
            _string(value.get("root_path"))
            or _string(value.get("current_project_root"))
            or _string(value.get("project_root"))
            or _string(current_project.get("root_path"))
        )
        raw_available_slots = value.get("available_slots", [])
        available_slots = [dict(item) for item in raw_available_slots if isinstance(item, dict)] if isinstance(raw_available_slots, list) else []
        raw_available_slot_ids = value.get("available_slot_ids", raw_available_slots)
        if isinstance(raw_available_slot_ids, list) and raw_available_slot_ids and isinstance(raw_available_slot_ids[0], dict):
            raw_available_slot_ids = [item.get("slot_id") for item in raw_available_slot_ids]

        raw_pending_approvals = value.get("pending_approvals", [])
        pending_approvals = [dict(item) for item in raw_pending_approvals if isinstance(item, dict)] if isinstance(raw_pending_approvals, list) else []
        raw_pending_approval_ids = value.get("pending_approval_ids", raw_pending_approvals)
        if isinstance(raw_pending_approval_ids, list) and raw_pending_approval_ids and isinstance(raw_pending_approval_ids[0], dict):
            raw_pending_approval_ids = [item.get("approval_id") for item in raw_pending_approval_ids]

        raw_recent_tasks = value.get("recent_tasks", [])
        recent_tasks = [dict(item) for item in raw_recent_tasks if isinstance(item, dict)] if isinstance(raw_recent_tasks, list) else []
        raw_recent_task_ids = value.get("recent_task_ids", raw_recent_tasks)
        if isinstance(raw_recent_task_ids, list) and raw_recent_task_ids and isinstance(raw_recent_task_ids[0], dict):
            raw_recent_task_ids = [item.get("task_id") for item in raw_recent_task_ids]

        return cls(
            root_path=root_path,
            requester_slot_id=_string(value.get("requester_slot_id")),
            confirmation_mode=_string(value.get("confirmation_mode")) or "conservative",
            cwd=_string(value.get("cwd")),
            available_slots=available_slots,
            available_slot_ids=_string_list(raw_available_slot_ids),
            pending_approvals=pending_approvals,
            pending_approval_ids=_string_list(raw_pending_approval_ids),
            latest_checkpoint_id=_string(value.get("latest_checkpoint_id")),
            recent_tasks=recent_tasks,
            recent_task_ids=_string_list(raw_recent_task_ids),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_path": self.root_path,
            "requester_slot_id": self.requester_slot_id,
            "confirmation_mode": self.confirmation_mode,
            "cwd": self.cwd,
            "available_slots": list(self.available_slots),
            "available_slot_ids": list(self.available_slot_ids),
            "pending_approvals": list(self.pending_approvals),
            "pending_approval_ids": list(self.pending_approval_ids),
            "latest_checkpoint_id": self.latest_checkpoint_id,
            "recent_tasks": list(self.recent_tasks),
            "recent_task_ids": list(self.recent_task_ids),
        }


@dataclass
class CommandInterpretation:
    intent: str
    confidence: float
    command_name: Optional[str]
    payload: Dict[str, Any] = field(default_factory=dict)
    missing_fields: List[str] = field(default_factory=list)
    ambiguous_references: List[Dict[str, Any]] = field(default_factory=list)
    resolved_references: List[Dict[str, Any]] = field(default_factory=list)
    needs_confirmation: bool = True
    confirmation_reason: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    validation_errors: List[str] = field(default_factory=list)
    ready_for_execution: bool = False
    inferred_fields: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "command_name": self.command_name,
            "payload": dict(self.payload),
            "missing_fields": list(self.missing_fields),
            "ambiguous_references": list(self.ambiguous_references),
            "resolved_references": list(self.resolved_references),
            "needs_confirmation": self.needs_confirmation,
            "confirmation_reason": self.confirmation_reason,
            "warnings": list(self.warnings),
            "validation_errors": list(self.validation_errors),
            "ready_for_execution": self.ready_for_execution,
            "inferred_fields": list(self.inferred_fields),
        }
