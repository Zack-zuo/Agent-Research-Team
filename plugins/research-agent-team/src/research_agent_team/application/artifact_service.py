from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

from research_agent_team.application.errors import CommandError
from research_agent_team.application.project_service import emit_event, load_project
from research_agent_team.domain import ArtifactVisibility
from research_agent_team.shared import new_id, now_utc
from research_agent_team.storage import ProjectLayout, append_jsonl


def _validate_relative_path(path_value: str) -> PurePosixPath:
    path = PurePosixPath(path_value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or "." in path.parts or ".." in path.parts:
        raise CommandError("invalid_artifact_path", "Artifact paths must be project-relative", path=path_value)
    return path


def _coerce_visibility(value: Any) -> ArtifactVisibility:
    try:
        return ArtifactVisibility(value)
    except ValueError:
        raise CommandError("invalid_artifact_visibility", f"Unsupported artifact visibility: {value}", visibility=value)


def _validate_output_path(slot_id: str, path: PurePosixPath, visibility: ArtifactVisibility) -> None:
    if visibility == ArtifactVisibility.PROJECT_SHARED:
        if path.parts[:1] == ("shared",) or path.parts[:2] == ("experiments", "runs"):
            return
        raise CommandError(
            "invalid_artifact_path",
            "Project-shared artifacts must live under shared/ or experiments/runs/",
            path=str(path),
        )
    if path.parts[:2] != ("agents", slot_id):
        raise CommandError(
            "invalid_artifact_path",
            "Slot-scoped artifacts must live under the producing slot directory",
            path=str(path),
        )


def _content_hash(layout: ProjectLayout, relative_path: PurePosixPath) -> str:
    digest = hashlib.sha256()
    with (layout.root / relative_path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_artifacts(layout: ProjectLayout) -> List[Dict[str, Any]]:
    if not layout.artifact_index.exists():
        return []
    artifacts: List[Dict[str, Any]] = []
    for line in layout.artifact_index.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            artifacts.append(parsed)
    return artifacts


def load_artifact(layout: ProjectLayout, artifact_id: str) -> Optional[Dict[str, Any]]:
    for artifact in reversed(list_artifacts(layout)):
        if artifact.get("artifact_id") == artifact_id:
            return artifact
    return None


def artifact_summary(artifact: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "artifact_id": artifact.get("artifact_id"),
        "type": artifact.get("type"),
        "path": artifact.get("path"),
        "visibility": artifact.get("visibility"),
        "producing_slot_id": artifact.get("producing_slot_id"),
        "producing_activation_id": artifact.get("producing_activation_id"),
        "task_id": artifact.get("task_id"),
        "created_at": artifact.get("created_at"),
    }


def recent_artifacts(layout: ProjectLayout, limit: int = 10) -> List[Dict[str, Any]]:
    return [artifact_summary(artifact) for artifact in list_artifacts(layout)[-limit:]]


def index_artifact(
    layout: ProjectLayout,
    *,
    path: str,
    artifact_type: str,
    visibility: ArtifactVisibility,
    producing_slot_id: Optional[str],
    producing_activation_id: Optional[str] = None,
    task_id: Optional[str] = None,
    source_artifact_ids: Optional[List[str]] = None,
    promoted_from_artifact_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    relative_path = _validate_relative_path(path)
    resolved = layout.project_relative_path(str(relative_path))
    if not resolved.exists():
        raise CommandError("artifact_path_not_found", f"Output artifact path does not exist: {path}", path=path)

    for source_artifact_id in source_artifact_ids or []:
        if load_artifact(layout, source_artifact_id) is None:
            raise CommandError("artifact_not_found", f"Source artifact does not exist: {source_artifact_id}", artifact_id=source_artifact_id)
    if promoted_from_artifact_id and load_artifact(layout, promoted_from_artifact_id) is None:
        raise CommandError(
            "artifact_not_found",
            f"Promoted source artifact does not exist: {promoted_from_artifact_id}",
            artifact_id=promoted_from_artifact_id,
        )

    explicit_artifact_id = artifact_id.strip() if isinstance(artifact_id, str) and artifact_id.strip() else None
    if explicit_artifact_id and load_artifact(layout, explicit_artifact_id) is not None:
        raise CommandError("artifact_id_conflict", f"Artifact ID already exists: {explicit_artifact_id}", artifact_id=explicit_artifact_id)

    timestamp = created_at or now_utc()
    artifact = {
        "artifact_id": explicit_artifact_id or new_id("artifact"),
        "type": artifact_type,
        "path": str(relative_path),
        "visibility": visibility.value,
        "producing_slot_id": producing_slot_id,
        "producing_activation_id": producing_activation_id,
        "task_id": task_id,
        "source_artifact_ids": list(source_artifact_ids or []),
        "promoted_from_artifact_id": promoted_from_artifact_id,
        "created_at": timestamp,
        "content_hash": _content_hash(layout, relative_path),
    }
    append_jsonl(layout.artifact_index, artifact)
    project = load_project(layout)
    emit_event(
        layout,
        project.project_id,
        "artifact.indexed",
        {"artifact_id": artifact["artifact_id"], "path": artifact["path"], "visibility": artifact["visibility"]},
        slot_id=producing_slot_id,
        task_id=task_id,
        activation_id=producing_activation_id,
    )
    return artifact


def publish_output_artifacts(layout: ProjectLayout, activation: Any, task: Any, descriptors: List[Dict[str, Any]]) -> List[str]:
    artifact_ids: List[str] = []
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            raise CommandError("invalid_payload", "output_artifacts entries must be objects", field="output_artifacts")
        raw_path = descriptor.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise CommandError("invalid_payload", "output artifact path is required", field="output_artifacts.path")
        if descriptor.get("artifact_id") is not None:
            raise CommandError("invalid_payload", "output artifact IDs are assigned by the runtime", field="output_artifacts.artifact_id")
        visibility = _coerce_visibility(descriptor.get("visibility", ArtifactVisibility.SLOT_PRIVATE.value))
        relative_path = _validate_relative_path(raw_path)
        _validate_output_path(activation.slot_id, relative_path, visibility)
        artifact = index_artifact(
            layout,
            path=str(relative_path),
            artifact_type=str(descriptor.get("type", "activation_output")),
            visibility=visibility,
            producing_slot_id=activation.slot_id,
            producing_activation_id=activation.activation_id,
            task_id=task.task_id,
            source_artifact_ids=list(descriptor.get("source_artifact_ids") or task.input_artifact_ids),
            promoted_from_artifact_id=descriptor.get("promoted_from_artifact_id"),
            artifact_id=descriptor.get("artifact_id"),
        )
        artifact_ids.append(artifact["artifact_id"])
    return artifact_ids
