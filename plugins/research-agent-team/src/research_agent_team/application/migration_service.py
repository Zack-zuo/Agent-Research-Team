from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from research_agent_team.application.adapter_registry import normalize_adapter_health
from research_agent_team.application.errors import CommandError
from research_agent_team.config import default_hook_config
from research_agent_team.domain import Event, MigrationRecord
from research_agent_team.shared import new_id, now_utc, utc_date
from research_agent_team.storage import ProjectLayout, SCHEMA_VERSION, append_jsonl, read_json, write_json_atomic, write_yaml_atomic
from research_agent_team.storage.schema import is_migratable_schema_version, read_schema_version


@dataclass
class MigrationPreparation:
    migration_performed: bool = False
    previous_schema_version: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    adapter_health: Dict[str, Any] = field(default_factory=dict)


def _raw_manifest(layout: ProjectLayout) -> Dict[str, Any]:
    if not layout.project_manifest.exists():
        return {}
    parsed = yaml.safe_load(layout.project_manifest.read_text(encoding="utf-8")) or {}
    if not isinstance(parsed, dict):
        raise CommandError("invalid_project_manifest", "project.yaml must contain a mapping")
    return parsed


def _append_schema_event(layout: ProjectLayout, project_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    timestamp = now_utc()
    event = Event(
        event_id=new_id("event"),
        event_type=event_type,
        created_at=timestamp,
        project_id=project_id,
        slot_id="supervisor",
        payload=payload,
    )
    append_jsonl(layout.events_dir / f"{utc_date(timestamp)}.jsonl", event.to_dict())


def _backup_existing_file(layout: ProjectLayout, migration_id: str, relative_path: str) -> None:
    source = layout.root / relative_path
    if not source.exists() or not source.is_file():
        return
    destination = layout.migration_backup_root(migration_id) / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def migrate_project_to_current_schema_locked(layout: ProjectLayout, from_schema_version: str) -> MigrationPreparation:
    if not is_migratable_schema_version(from_schema_version):
        raise CommandError(
            "unsupported_schema_version",
            f"Unsupported schema version: {from_schema_version}",
            expected_schema_version=SCHEMA_VERSION,
            actual_schema_version=from_schema_version,
        )

    manifest = _raw_manifest(layout)
    project = read_json(layout.project_state)
    project_id = str(project.get("project_id") or "unknown-project")
    migration_id = new_id("migration")
    started_at = now_utc()
    mutated_paths = [
        "project.yaml",
        "state/project.json",
        "state/adapters/health.json",
        "state/hooks/config.json",
    ]
    if (layout.state_dir / "policies" / "approvals.json").exists():
        mutated_paths.append("state/policies/approvals.json")

    _append_schema_event(
        layout,
        project_id,
        "schema.migration_started",
        {
            "migration_id": migration_id,
            "from_schema_version": from_schema_version,
            "to_schema_version": SCHEMA_VERSION,
        },
    )
    for relative_path in mutated_paths:
        _backup_existing_file(layout, migration_id, relative_path)

    manifest["schema_version"] = SCHEMA_VERSION
    write_yaml_atomic(layout.project_manifest, manifest)

    project["schema_version"] = SCHEMA_VERSION
    write_json_atomic(layout.project_state, project)

    adapter_health = normalize_adapter_health(read_json(layout.adapter_health) if layout.adapter_health.exists() else {})
    write_json_atomic(layout.adapter_health, adapter_health)
    if not layout.hook_config.exists():
        write_json_atomic(layout.hook_config, default_hook_config())

    completed_at = now_utc()
    record = MigrationRecord(
        migration_id=migration_id,
        project_id=project_id,
        from_schema_version=from_schema_version,
        to_schema_version=SCHEMA_VERSION,
        status="completed",
        backup_root=str(layout.migration_backup_root(migration_id).relative_to(layout.root)),
        mutated_paths=mutated_paths,
        warnings=[],
        started_at=started_at,
        completed_at=completed_at,
    )
    write_json_atomic(layout.migration_record_path(migration_id), record.to_dict())
    _append_schema_event(
        layout,
        project_id,
        "schema.migrated",
        {
            "migration_id": migration_id,
            "from_schema_version": from_schema_version,
            "to_schema_version": SCHEMA_VERSION,
        },
    )
    return MigrationPreparation(
        migration_performed=True,
        previous_schema_version=from_schema_version,
        warnings=[f"Migrated schema from {from_schema_version} to {SCHEMA_VERSION}"],
        adapter_health=adapter_health,
    )


def prepare_project_schema_locked(layout: ProjectLayout) -> MigrationPreparation:
    manifest = _raw_manifest(layout)
    schema_version = read_schema_version(manifest)
    if schema_version == SCHEMA_VERSION:
        return MigrationPreparation(
            migration_performed=False,
            previous_schema_version=None,
            warnings=[],
            adapter_health=normalize_adapter_health(read_json(layout.adapter_health) if layout.adapter_health.exists() else {}),
        )
    if is_migratable_schema_version(schema_version):
        return migrate_project_to_current_schema_locked(layout, schema_version)
    raise CommandError(
        "unsupported_schema_version",
        f"Unsupported schema version: {schema_version}",
        expected_schema_version=SCHEMA_VERSION,
        actual_schema_version=schema_version,
    )
