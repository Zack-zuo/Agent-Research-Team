from __future__ import annotations

from pathlib import Path
from typing import Iterable, List


def _validate_slot_id(slot_id: str) -> None:
    if not slot_id or "/" in slot_id or "\\" in slot_id or slot_id in {".", ".."}:
        raise ValueError(f"invalid slot_id: {slot_id}")


class ProjectLayout:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()

    @property
    def project_manifest(self) -> Path:
        return self.root / "project.yaml"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def project_state(self) -> Path:
        return self.state_dir / "project.json"

    @property
    def topology_state(self) -> Path:
        return self.state_dir / "topology" / "current.json"

    @property
    def budget_state(self) -> Path:
        return self.state_dir / "budgets" / "current.json"

    @property
    def adapter_config(self) -> Path:
        return self.state_dir / "adapters" / "config.json"

    @property
    def adapter_health(self) -> Path:
        return self.state_dir / "adapters" / "health.json"

    @property
    def hook_config(self) -> Path:
        return self.state_dir / "hooks" / "config.json"

    @property
    def hook_logs_dir(self) -> Path:
        return self.root / "logs" / "hooks"

    @property
    def events_dir(self) -> Path:
        return self.state_dir / "events"

    @property
    def messages_dir(self) -> Path:
        return self.state_dir / "messages"

    @property
    def artifact_index(self) -> Path:
        return self.state_dir / "artifacts" / "index.jsonl"

    @property
    def migration_records_dir(self) -> Path:
        return self.state_dir / "migrations" / "records"

    @property
    def migration_backups_dir(self) -> Path:
        return self.state_dir / "migrations" / "backups"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "locks" / "project.lock"

    def migration_backup_root(self, migration_id: str) -> Path:
        _validate_slot_id(migration_id)
        return self.migration_backups_dir / migration_id

    def migration_record_path(self, migration_id: str) -> Path:
        _validate_slot_id(migration_id)
        return self.migration_records_dir / f"{migration_id}.json"

    def slot_state_path(self, slot_id: str) -> Path:
        _validate_slot_id(slot_id)
        return self.state_dir / "slots" / f"{slot_id}.json"

    def task_state_path(self, task_id: str) -> Path:
        _validate_slot_id(task_id)
        return self.state_dir / "tasks" / f"{task_id}.json"

    def activation_state_path(self, activation_id: str) -> Path:
        _validate_slot_id(activation_id)
        return self.state_dir / "activations" / f"{activation_id}.json"

    def slot_checkpoint_state_path(self, slot_id: str) -> Path:
        _validate_slot_id(slot_id)
        return self.state_dir / "checkpoints" / f"{slot_id}.json"

    def experiment_request_state_path(self, experiment_request_id: str) -> Path:
        _validate_slot_id(experiment_request_id)
        return self.state_dir / "experiments" / "requests" / f"{experiment_request_id}.json"

    def experiment_run_state_path(self, experiment_run_id: str) -> Path:
        _validate_slot_id(experiment_run_id)
        return self.state_dir / "experiments" / "runs" / f"{experiment_run_id}.json"

    def experiment_comparison_state_path(self, comparison_id: str) -> Path:
        _validate_slot_id(comparison_id)
        return self.state_dir / "experiments" / "comparisons" / f"{comparison_id}.json"

    def experiment_review_state_path(self, review_id: str) -> Path:
        _validate_slot_id(review_id)
        return self.state_dir / "experiments" / "reviews" / f"{review_id}.json"

    def slot_root(self, slot_id: str) -> Path:
        _validate_slot_id(slot_id)
        return self.root / "agents" / slot_id

    def slot_workspace(self, slot_id: str) -> Path:
        return self.slot_root(slot_id) / "workspace"

    def slot_activation_root(self, slot_id: str, activation_id: str) -> Path:
        _validate_slot_id(activation_id)
        return self.slot_root(slot_id) / "activations" / activation_id

    def slot_checkpoint_root(self, slot_id: str, checkpoint_id: str) -> Path:
        _validate_slot_id(checkpoint_id)
        return self.slot_root(slot_id) / "checkpoints" / checkpoint_id

    def experiment_queue_root(self, experiment_request_id: str) -> Path:
        _validate_slot_id(experiment_request_id)
        return self.root / "experiments" / "queue" / experiment_request_id

    def experiment_run_root(self, experiment_run_id: str) -> Path:
        _validate_slot_id(experiment_run_id)
        return self.root / "experiments" / "runs" / experiment_run_id

    def project_relative_path(self, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"path escapes project root: {relative_path}")
        resolved = (self.root / candidate).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError(f"path escapes project root: {relative_path}")
        return resolved

    def canonical_directories(self) -> List[Path]:
        relative_dirs = [
            "state/topology",
            "state/slots",
            "state/tasks",
            "state/activations",
            "state/approvals",
            "state/budgets",
            "state/policies",
            "state/checkpoints",
            "state/adapters",
            "state/knowledge/slots",
            "state/experiments/requests",
            "state/experiments/runs",
            "state/experiments/comparisons",
            "state/experiments/reviews",
            "state/migrations/records",
            "state/migrations/backups",
            "state/hooks",
            "state/events",
            "state/messages",
            "state/artifacts",
            "state/locks",
            "logs/hooks",
            "shared/raw",
            "shared/wiki",
            "shared/graph",
            "shared/artifacts",
            "shared/reports",
            "experiments/queue",
            "experiments/runs",
            "experiments/baselines",
        ]
        return [self.root / relative_dir for relative_dir in relative_dirs]

    def create_base_layout(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in self.canonical_directories():
            directory.mkdir(parents=True, exist_ok=True)

    def create_slot_layout(self, slot_id: str) -> None:
        for directory_name in ["checkpoints", "activations", "workspace", "kb", "inbox", "outbox"]:
            (self.slot_root(slot_id) / directory_name).mkdir(parents=True, exist_ok=True)


def ensure_support_surfaces(layout: ProjectLayout, slot_ids: Iterable[str]) -> List[str]:
    warnings: List[str] = []
    for directory in layout.canonical_directories():
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
            warnings.append(f"recreated support directory: {directory.relative_to(layout.root)}")
    for slot_id in slot_ids:
        for directory_name in ["checkpoints", "activations", "workspace", "kb", "inbox", "outbox"]:
            path = layout.slot_root(slot_id) / directory_name
            if not path.exists():
                path.mkdir(parents=True, exist_ok=True)
                warnings.append(f"recreated slot directory: {path.relative_to(layout.root)}")
    return warnings
