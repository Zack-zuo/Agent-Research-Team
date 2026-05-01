from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from research_agent_team.application.artifact_service import artifact_summary, index_artifact, list_artifacts
from research_agent_team.application.errors import CommandError
from research_agent_team.application.project_service import _require_string, emit_event, load_project, load_topology
from research_agent_team.domain import ArtifactVisibility
from research_agent_team.integrations.graph import LocalFileGraphAdapter
from research_agent_team.shared import now_utc
from research_agent_team.storage import ProjectLayout, ensure_support_surfaces, project_lock, read_json, write_json_atomic, write_text_atomic


GRAPH_MODES = {"incremental", "full"}
MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdown", ".mkd"}


def _layout_from_payload(payload: Dict[str, Any]) -> ProjectLayout:
    return ProjectLayout(Path(_require_string(payload, "root_path")))


def _mode(payload: Dict[str, Any]) -> str:
    value = payload.get("mode", "incremental")
    if not isinstance(value, str) or value not in GRAPH_MODES:
        raise CommandError("invalid_mode", f"Unsupported graph rebuild mode: {value}", mode=value)
    return value


def _safe_timestamp(timestamp: str) -> str:
    return timestamp.replace(":", "").replace("-", "").replace(".", "").replace("+", "").replace("Z", "Z")


def _latest_artifact_for_path(layout: ProjectLayout, relative_path: str) -> Optional[Dict[str, Any]]:
    for artifact in reversed(list_artifacts(layout)):
        if artifact.get("path") == relative_path:
            return artifact
    return None


def _project_relative(layout: ProjectLayout, path: Path) -> str:
    return path.resolve().relative_to(layout.root).as_posix()


def _project_wiki_documents(layout: ProjectLayout) -> List[Path]:
    wiki_root = layout.root / "shared" / "wiki"
    if not wiki_root.exists():
        return []
    return sorted(path for path in wiki_root.rglob("*") if path.is_file() and path.suffix.lower() in MARKDOWN_EXTENSIONS)


def _source_artifact_ids(layout: ProjectLayout, documents: List[Path]) -> List[str]:
    ids: List[str] = []
    for document in documents:
        artifact = _latest_artifact_for_path(layout, _project_relative(layout, document))
        if artifact and artifact.get("artifact_id"):
            ids.append(artifact["artifact_id"])
    return ids


def _read_adapter_config(layout: ProjectLayout) -> Dict[str, Any]:
    if not layout.adapter_config.exists():
        return {}
    return read_json(layout.adapter_config).get("graph", {})


def _write_graph_health(layout: ProjectLayout, *, status: str, message: str) -> None:
    health = read_json(layout.adapter_health) if layout.adapter_health.exists() else {}
    health["graph"] = {"status": status, "message": message, "updated_at": now_utc()}
    write_json_atomic(layout.adapter_health, health)


def _mark_project_graph_clean(layout: ProjectLayout, timestamp: str) -> None:
    knowledge_path = layout.state_dir / "knowledge" / "project.json"
    state = read_json(knowledge_path) if knowledge_path.exists() else {}
    state["graph_dirty"] = False
    state["last_graph_rebuilt_at"] = timestamp
    write_json_atomic(knowledge_path, state)


def _degraded(layout: ProjectLayout, project_id: str, *, mode: str, message: str, warnings: List[str] = None) -> Dict[str, Any]:
    _write_graph_health(layout, status="degraded", message=message)
    hook_warnings = emit_event(
        layout,
        project_id,
        "graph.rebuild_degraded",
        {"mode": mode, "message": message},
        slot_id="supervisor",
        dispatch_hooks=True,
    )
    all_warnings = list(warnings or []) + [message] + hook_warnings
    return {
        "rebuilt": False,
        "degraded": True,
        "mode": mode,
        "export_path": None,
        "timestamped_export_path": None,
        "report_path": None,
        "timestamped_report_path": None,
        "artifacts": [],
        "node_count": 0,
        "edge_count": 0,
        "warnings": all_warnings,
    }


def rebuild_graph(payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_payload(payload)
    mode = _mode(payload)
    with project_lock(layout.lock_path):
        from research_agent_team.application.health_service import prepare_project_for_command_locked

        preparation = prepare_project_for_command_locked(layout)
        project = load_project(layout)
        graph_config = _read_adapter_config(layout)
        if graph_config.get("enabled") is False:
            return _degraded(layout, project.project_id, mode=mode, message="Graph adapter is disabled.", warnings=preparation.warnings)
        adapter_name = graph_config.get("adapter", "local_file")
        if adapter_name != "local_file":
            return _degraded(
                layout,
                project.project_id,
                mode=mode,
                message=f"Graph adapter is unavailable: {adapter_name}",
                warnings=preparation.warnings,
            )

        timestamp = now_utc()
        documents = _project_wiki_documents(layout)
        adapter = LocalFileGraphAdapter()
        try:
            export, report = adapter.build(root=layout.root, document_paths=documents, generated_at=timestamp, mode=mode)
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            return _degraded(layout, project.project_id, mode=mode, message=f"Graph adapter failed: {exc}", warnings=preparation.warnings)

        timestamp_slug = _safe_timestamp(timestamp)
        timestamped_export_path = f"shared/graph/graph-export-{timestamp_slug}.json"
        latest_export_path = "shared/graph/graph-export-latest.json"
        timestamped_report_path = f"shared/graph/graph-report-{timestamp_slug}.md"
        latest_report_path = "shared/graph/graph-report-latest.md"

        write_json_atomic(layout.root / timestamped_export_path, export)
        write_json_atomic(layout.root / latest_export_path, export)
        write_text_atomic(layout.root / timestamped_report_path, report)
        write_text_atomic(layout.root / latest_report_path, report)

        source_ids = _source_artifact_ids(layout, documents)
        export_artifact = index_artifact(
            layout,
            path=latest_export_path,
            artifact_type="graph_export",
            visibility=ArtifactVisibility.PROJECT_SHARED,
            producing_slot_id="supervisor",
            source_artifact_ids=source_ids,
            created_at=timestamp,
        )
        report_artifact = index_artifact(
            layout,
            path=latest_report_path,
            artifact_type="graph_report",
            visibility=ArtifactVisibility.PROJECT_SHARED,
            producing_slot_id="supervisor",
            source_artifact_ids=source_ids,
            created_at=timestamp,
        )
        _mark_project_graph_clean(layout, timestamp)
        _write_graph_health(layout, status="healthy", message="Local-file graph adapter rebuilt project graph.")
        hook_warnings = emit_event(
            layout,
            project.project_id,
            "graph.rebuilt",
            {"mode": mode, "node_count": export["node_count"], "edge_count": export["edge_count"], "path": latest_export_path},
            slot_id="supervisor",
            dispatch_hooks=True,
        )
        return {
            "rebuilt": True,
            "degraded": False,
            "mode": mode,
            "export_path": latest_export_path,
            "timestamped_export_path": timestamped_export_path,
            "report_path": latest_report_path,
            "timestamped_report_path": timestamped_report_path,
            "artifacts": [artifact_summary(export_artifact), artifact_summary(report_artifact)],
            "node_count": export["node_count"],
            "edge_count": export["edge_count"],
            "warnings": preparation.warnings + hook_warnings,
        }
