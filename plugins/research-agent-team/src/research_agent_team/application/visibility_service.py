from __future__ import annotations

from typing import Any, Dict, List

from research_agent_team.application.artifact_service import load_artifact
from research_agent_team.application.errors import CommandError
from research_agent_team.application.project_service import _read_slot
from research_agent_team.domain import ArtifactVisibility
from research_agent_team.storage import ProjectLayout


def can_slot_read_artifact(layout: ProjectLayout, slot_id: str, artifact: Dict[str, Any]) -> bool:
    producing_slot_id = artifact.get("producing_slot_id")
    if producing_slot_id == slot_id:
        return True
    visibility = artifact.get("visibility")
    if visibility == ArtifactVisibility.PROJECT_SHARED.value:
        return True
    if visibility != ArtifactVisibility.ANCESTOR_VISIBLE.value:
        return False
    if not isinstance(producing_slot_id, str) or not producing_slot_id:
        return False
    try:
        reader = _read_slot(layout, slot_id)
    except CommandError:
        return False
    return producing_slot_id in reader.descendant_slot_ids


def resolve_attached_artifacts(layout: ProjectLayout, requester_slot_id: str, owner_slot_id: str, artifact_ids: List[str]) -> List[Dict[str, Any]]:
    artifacts: List[Dict[str, Any]] = []
    for artifact_id in artifact_ids:
        artifact = load_artifact(layout, artifact_id)
        if artifact is None:
            raise CommandError("artifact_not_found", f"Artifact does not exist: {artifact_id}", artifact_id=artifact_id)
        if not can_slot_read_artifact(layout, requester_slot_id, artifact):
            raise CommandError(
                "artifact_not_visible",
                f"Requester slot cannot read artifact: {artifact_id}",
                artifact_id=artifact_id,
                slot_id=requester_slot_id,
            )
        if not can_slot_read_artifact(layout, owner_slot_id, artifact):
            raise CommandError(
                "artifact_not_visible",
                f"Owner slot cannot read artifact: {artifact_id}",
                artifact_id=artifact_id,
                slot_id=owner_slot_id,
            )
        artifacts.append(artifact)
    return artifacts


def build_granted_permissions(task: Any, attached_artifacts: List[Dict[str, Any]]) -> List[str]:
    grants = {"write_slot_workspace", "write_slot_kb"}
    if any(path_root == "shared" or path_root.startswith("shared/") for path_root in task.input_path_roots):
        grants.add("read_shared_artifacts")
    if any(artifact.get("visibility") == ArtifactVisibility.PROJECT_SHARED.value for artifact in attached_artifacts):
        grants.add("read_shared_artifacts")
    if any(artifact.get("visibility") == ArtifactVisibility.SLOT_PRIVATE.value for artifact in attached_artifacts):
        grants.add("read_attached_private_artifacts")
    if task.review_requirement == "experiment_review":
        grants.add("request_experiment_run")
    return sorted(grants)


def permission_manifest(task: Any, activation: Any, attached_artifacts: List[Dict[str, Any]], granted_permissions: List[str]) -> Dict[str, Any]:
    return {
        "activation_id": activation.activation_id,
        "task_id": task.task_id,
        "slot_id": activation.slot_id,
        "allowed_artifact_ids": list(task.input_artifact_ids),
        "allowed_path_roots": list(task.input_path_roots),
        "granted_permissions": list(granted_permissions),
        "artifact_access": [
            {
                "artifact_id": artifact.get("artifact_id"),
                "path": artifact.get("path"),
                "visibility": artifact.get("visibility"),
                "producing_slot_id": artifact.get("producing_slot_id"),
            }
            for artifact in attached_artifacts
        ],
    }
