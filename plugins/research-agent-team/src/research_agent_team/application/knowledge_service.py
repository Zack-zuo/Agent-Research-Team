from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from research_agent_team.application.artifact_service import artifact_summary, index_artifact, list_artifacts
from research_agent_team.application.errors import CommandError
from research_agent_team.application.project_service import (
    _read_slot,
    _require_string,
    emit_event,
    load_project,
    load_topology,
)
from research_agent_team.domain import ArtifactVisibility
from research_agent_team.shared import now_utc
from research_agent_team.storage import ProjectLayout, ensure_support_surfaces, project_lock, read_json, write_json_atomic, write_text_atomic


SYNC_MODES = {"incremental", "full"}
SYNC_SCOPE_TYPES = {"project", "slot"}
MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdown", ".mkd"}
LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _layout_from_payload(payload: Dict[str, Any]) -> ProjectLayout:
    return ProjectLayout(Path(_require_string(payload, "root_path")))


def _optional_string(payload: Dict[str, Any], key: str) -> Optional[str]:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CommandError("invalid_payload", f"{key} must be a string", field=key)
    return value.strip()


def _mode(payload: Dict[str, Any]) -> str:
    value = payload.get("mode", "incremental")
    if not isinstance(value, str) or value not in SYNC_MODES:
        raise CommandError("invalid_mode", f"Unsupported knowledge sync mode: {value}", mode=value)
    return value


def _project_relative(layout: ProjectLayout, path: Path) -> str:
    return path.resolve().relative_to(layout.root).as_posix()


def _safe_output_path(source_relative_path: str) -> str:
    source = Path(source_relative_path.replace("\\", "/"))
    suffix = source.suffix.lower()
    if suffix in MARKDOWN_EXTENSIONS:
        return source.as_posix()
    return f"{source.as_posix()}.md"


def _content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_text_or_stub(path: Path, source_relative_path: str) -> tuple[str, bool]:
    try:
        return path.read_text(encoding="utf-8"), True
    except UnicodeDecodeError:
        return f"Binary or non-UTF-8 source captured at `{source_relative_path}`.\n", False


def _title_from_markdown(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
    return fallback


def _markdown_links(text: str) -> List[str]:
    links: List[str] = []
    for match in LINK_PATTERN.finditer(text):
        target = match.group(1).strip()
        if target and not target.startswith(("http://", "https://", "mailto:", "#")):
            links.append(target)
    return links


def _slot_id_from_source(source_relative_path: str) -> Optional[str]:
    parts = Path(source_relative_path).parts
    if len(parts) >= 2 and parts[0] == "agents":
        return parts[1]
    return None


def _latest_artifact_for_path(layout: ProjectLayout, relative_path: str) -> Optional[Dict[str, Any]]:
    for artifact in reversed(list_artifacts(layout)):
        if artifact.get("path") == relative_path:
            return artifact
    return None


def _index_raw_source_if_needed(layout: ProjectLayout, source_relative_path: str) -> Optional[str]:
    if not source_relative_path.startswith("shared/raw/"):
        latest = _latest_artifact_for_path(layout, source_relative_path)
        return latest.get("artifact_id") if latest else None
    latest = _latest_artifact_for_path(layout, source_relative_path)
    source_hash = _content_hash(layout.root / source_relative_path)
    if latest and latest.get("content_hash") == source_hash:
        return latest.get("artifact_id")
    artifact = index_artifact(
        layout,
        path=source_relative_path,
        artifact_type="raw_source",
        visibility=ArtifactVisibility.PROJECT_SHARED,
        producing_slot_id="supervisor",
    )
    return artifact["artifact_id"]


def _iter_files(roots: Iterable[Path], layout: ProjectLayout) -> List[Path]:
    files: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file():
                try:
                    layout.project_relative_path(_project_relative(layout, path))
                except ValueError:
                    continue
                files.append(path)
    return files


def _slot_sources(layout: ProjectLayout, slot_id: str) -> List[Path]:
    slot_root = layout.slot_root(slot_id)
    kb_root = slot_root / "kb"
    sources = _iter_files([layout.root / "shared" / "raw", slot_root / "workspace", kb_root], layout)
    filtered: List[Path] = []
    for path in sources:
        try:
            if "wiki" in path.relative_to(kb_root).parts:
                continue
        except ValueError:
            pass
        filtered.append(path)
    return filtered


def _project_sources(layout: ProjectLayout) -> List[Path]:
    raw_sources = _iter_files([layout.root / "shared" / "raw"], layout)
    slot_wiki_roots = sorted((layout.root / "agents").glob("*/kb/wiki"))
    return raw_sources + _iter_files(slot_wiki_roots, layout)


def _compile_source_page(
    *,
    layout: ProjectLayout,
    source_path: Path,
    output_relative_path: str,
    scope_type: str,
    owner_slot_id: Optional[str],
    timestamp: str,
) -> tuple[str, Dict[str, Any]]:
    source_relative_path = _project_relative(layout, source_path)
    source_text, is_text = _read_text_or_stub(source_path, source_relative_path)
    title = _title_from_markdown(source_text, Path(source_relative_path).stem)
    links = _markdown_links(source_text) if is_text else []
    lines = [
        f"# {title}",
        "",
        f"- Source: `{source_relative_path}`",
        f"- Scope: `{scope_type}`",
        f"- Owner Slot: `{owner_slot_id or 'project'}`",
        f"- Compiled At: `{timestamp}`",
        "",
        "## Content",
        "",
        source_text.strip() or "_No textual content._",
        "",
        "## Source References",
        f"- `{source_relative_path}`",
    ]
    if links:
        lines.extend(["", "## Backlinks"])
        for link in links:
            lines.append(f"- `{link}`")
    return "\n".join(lines) + "\n", {"source": source_relative_path, "links": links, "output": output_relative_path}


def _write_index(
    layout: ProjectLayout,
    *,
    output_relative_path: str,
    scope_title: str,
    entries: List[Dict[str, Any]],
    timestamp: str,
) -> None:
    lines = [
        f"# {scope_title} Knowledge Index",
        "",
        f"- Compiled At: `{timestamp}`",
        "",
        "## Pages",
    ]
    for entry in entries:
        lines.append(f"- `{entry['source']}` -> `{entry['output']}`")
    if not entries:
        lines.append("- none")
    write_text_atomic(layout.root / output_relative_path, "\n".join(lines) + "\n")


def _load_knowledge_state(layout: ProjectLayout, scope_type: str, scope_id: str) -> Dict[str, Any]:
    path = _knowledge_state_path(layout, scope_type, scope_id)
    if path.exists():
        return read_json(path)
    return {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "source_hashes": {},
        "compiled_artifact_ids_by_output_path": {},
        "backlinks": {},
        "last_attempted_at": None,
        "last_synced_at": None,
        "last_error": None,
        "graph_dirty": False,
    }


def _knowledge_state_path(layout: ProjectLayout, scope_type: str, scope_id: str) -> Path:
    if scope_type == "project":
        return layout.state_dir / "knowledge" / "project.json"
    return layout.state_dir / "knowledge" / "slots" / f"{scope_id}.json"


def _write_knowledge_state(
    layout: ProjectLayout,
    *,
    scope_type: str,
    scope_id: str,
    state: Dict[str, Any],
) -> None:
    write_json_atomic(_knowledge_state_path(layout, scope_type, scope_id), state)


def _remove_stale_outputs(layout: ProjectLayout, *, output_root_relative: str, previous_paths: Iterable[str], current_paths: set[str]) -> None:
    output_root = layout.root / output_root_relative
    for previous_path in previous_paths:
        if previous_path in current_paths or not previous_path.startswith(f"{output_root_relative}/"):
            continue
        path = layout.project_relative_path(previous_path)
        if path.is_file():
            path.unlink()
            parent = path.parent
            while parent != output_root and output_root in parent.parents:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent


def _compile_sources(
    layout: ProjectLayout,
    *,
    scope_type: str,
    scope_id: str,
    sources: List[Path],
    output_root_relative: str,
    visibility: ArtifactVisibility,
    mode: str,
    timestamp: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    state = _load_knowledge_state(layout, scope_type, scope_id)
    previous_output_paths = set((state.get("compiled_artifact_ids_by_output_path") or {}).keys())
    state["last_attempted_at"] = timestamp
    state["last_error"] = None
    source_hashes: Dict[str, str] = {}
    compiled_by_path: Dict[str, str] = {}
    backlinks: Dict[str, List[str]] = {}
    compiled_artifacts: List[Dict[str, Any]] = []
    index_entries: List[Dict[str, Any]] = []

    for source_path in sources:
        source_relative_path = _project_relative(layout, source_path)
        source_hashes[source_relative_path] = _content_hash(source_path)
        page_relative_path = f"{output_root_relative}/{_safe_output_path(source_relative_path)}"
        owner_slot_id = _slot_id_from_source(source_relative_path) or (scope_id if scope_type == "slot" else None)
        content, entry = _compile_source_page(
            layout=layout,
            source_path=source_path,
            output_relative_path=page_relative_path,
            scope_type=scope_type,
            owner_slot_id=owner_slot_id,
            timestamp=timestamp,
        )
        write_text_atomic(layout.root / page_relative_path, content)
        source_artifact_id = _index_raw_source_if_needed(layout, source_relative_path)
        promoted_from = _latest_artifact_for_path(layout, source_relative_path)
        source_artifact_ids = [source_artifact_id] if source_artifact_id else []
        artifact = index_artifact(
            layout,
            path=page_relative_path,
            artifact_type="knowledge_page",
            visibility=visibility,
            producing_slot_id=owner_slot_id or "supervisor",
            source_artifact_ids=source_artifact_ids,
            promoted_from_artifact_id=promoted_from.get("artifact_id") if promoted_from else None,
            created_at=timestamp,
        )
        compiled_by_path[page_relative_path] = artifact["artifact_id"]
        backlinks[page_relative_path] = [source_relative_path] + entry["links"]
        compiled_artifacts.append(artifact_summary(artifact))
        index_entries.append(entry)

    index_relative_path = f"{output_root_relative}/index.md"
    _write_index(
        layout,
        output_relative_path=index_relative_path,
        scope_title="Project" if scope_type == "project" else f"Slot {scope_id}",
        entries=index_entries,
        timestamp=timestamp,
    )
    index_artifact_record = index_artifact(
        layout,
        path=index_relative_path,
        artifact_type="knowledge_index",
        visibility=visibility,
        producing_slot_id="supervisor" if scope_type == "project" else scope_id,
        source_artifact_ids=[artifact["artifact_id"] for artifact in compiled_artifacts],
        created_at=timestamp,
    )
    compiled_by_path[index_relative_path] = index_artifact_record["artifact_id"]
    backlinks[index_relative_path] = [entry["source"] for entry in index_entries]
    compiled_artifacts.append(artifact_summary(index_artifact_record))

    if mode == "full":
        _remove_stale_outputs(
            layout,
            output_root_relative=output_root_relative,
            previous_paths=previous_output_paths,
            current_paths=set(compiled_by_path),
        )

    state["source_hashes"] = source_hashes
    state["compiled_artifact_ids_by_output_path"] = compiled_by_path
    state["backlinks"] = backlinks
    state["last_synced_at"] = timestamp
    state["graph_dirty"] = True
    _write_knowledge_state(layout, scope_type=scope_type, scope_id=scope_id, state=state)
    return compiled_artifacts, state


def sync_knowledge_base(payload: Dict[str, Any]) -> Dict[str, Any]:
    layout = _layout_from_payload(payload)
    scope_type = _require_string(payload, "scope_type")
    if scope_type not in SYNC_SCOPE_TYPES:
        raise CommandError("invalid_scope_type", f"Unsupported knowledge scope_type: {scope_type}", scope_type=scope_type)
    mode = _mode(payload)
    scope_id = _optional_string(payload, "scope_id")

    with project_lock(layout.lock_path):
        from research_agent_team.application.health_service import prepare_project_for_command_locked

        preparation = prepare_project_for_command_locked(layout)
        project = load_project(layout)
        timestamp = now_utc()
        if scope_type == "slot":
            if scope_id is None:
                raise CommandError("invalid_payload", "scope_id is required for slot knowledge sync", field="scope_id")
            slot = _read_slot(layout, scope_id)
            sources = _slot_sources(layout, slot.slot_id)
            compiled_artifacts, state = _compile_sources(
                layout,
                scope_type="slot",
                scope_id=slot.slot_id,
                sources=sources,
                output_root_relative=f"agents/{slot.slot_id}/kb/wiki",
                visibility=ArtifactVisibility.ANCESTOR_VISIBLE,
                mode=mode,
                timestamp=timestamp,
            )
        else:
            scope_id = project.project_id
            sources = _project_sources(layout)
            compiled_artifacts, state = _compile_sources(
                layout,
                scope_type="project",
                scope_id=scope_id,
                sources=sources,
                output_root_relative="shared/wiki",
                visibility=ArtifactVisibility.PROJECT_SHARED,
                mode=mode,
                timestamp=timestamp,
            )

        hook_warnings = emit_event(
            layout,
            project.project_id,
            "knowledge.synced",
            {"scope_type": scope_type, "scope_id": scope_id, "mode": mode, "compiled_count": len(compiled_artifacts)},
            slot_id=scope_id if scope_type == "slot" else "supervisor",
            dispatch_hooks=True,
        )
        return {
            "synced": True,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "mode": mode,
            "source_count": len(sources),
            "compiled_count": len(compiled_artifacts),
            "compiled_artifacts": compiled_artifacts,
            "graph_dirty": state["graph_dirty"],
            "warnings": preparation.warnings + hook_warnings,
        }
