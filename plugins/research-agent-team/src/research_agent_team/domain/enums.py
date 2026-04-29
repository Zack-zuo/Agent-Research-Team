from __future__ import annotations

from enum import Enum


class OperatingMode(str, Enum):
    AUTONOMOUS = "autonomous"
    SEMI_AUTONOMOUS = "semi_autonomous"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class SlotRole(str, Enum):
    SUPERVISOR = "supervisor"
    SENIOR_PHD = "senior_phd"
    JUNIOR_PHD = "junior_phd"


class SlotStatus(str, Enum):
    ACTIVE = "active"
    RETIRING = "retiring"
    RETIRED = "retired"
    ARCHIVED = "archived"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    ADMITTED = "admitted"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    AWAITING_APPROVAL = "awaiting_approval"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ActivationStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalScopeType(str, Enum):
    ADD_SENIOR = "add_senior"
    ADD_JUNIOR = "add_junior"
    RETIRE_SENIOR = "retire_senior"
    RETIRE_JUNIOR = "retire_junior"
    TASK_BUDGET_OVERRIDE = "task_budget_override"
    EXPERIMENT_BATCH = "experiment_batch"
    TASK_RUNTIME_GATE = "task_runtime_gate"
    GENERATE_REPORT = "generate_report"
    ARCHIVE_PROJECT = "archive_project"


class ApprovalApproverType(str, Enum):
    SUPERVISOR = "supervisor"
    USER = "user"


class ArtifactVisibility(str, Enum):
    SLOT_PRIVATE = "slot_private"
    ANCESTOR_VISIBLE = "ancestor_visible"
    PROJECT_SHARED = "project_shared"
