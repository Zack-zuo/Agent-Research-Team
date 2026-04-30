from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List


LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


class LocalFileGraphAdapter:
    adapter_id = "local_file"

    def build(self, *, root: Path, document_paths: List[Path], generated_at: str, mode: str) -> tuple[Dict[str, Any], str]:
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, str]] = []

        for path in sorted(document_paths):
            relative_path = path.resolve().relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8")
            title = self._title(text, path.stem)
            nodes.append({"id": relative_path, "path": relative_path, "title": title})
            for label, target in LINK_PATTERN.findall(text):
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                edges.append(
                    {
                        "source": relative_path,
                        "target": self._resolve_link(root, path, target),
                        "label": label.strip(),
                        "type": "markdown_link",
                    }
                )

        export = {
            "adapter_id": self.adapter_id,
            "mode": mode,
            "generated_at": generated_at,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes": nodes,
            "edges": edges,
            "provenance": {"document_paths": [node["path"] for node in nodes]},
        }
        report = self._report(export)
        return export, report

    def _title(self, text: str, fallback: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip()
                if title:
                    return title
        return fallback

    def _resolve_link(self, root: Path, source_path: Path, target: str) -> str:
        target_path = Path(target)
        if target_path.is_absolute():
            return target
        resolved = (source_path.parent / target_path).resolve()
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            return target

    def _report(self, export: Dict[str, Any]) -> str:
        lines = [
            "# Graph Report",
            "",
            f"- Adapter: `{export['adapter_id']}`",
            f"- Mode: `{export['mode']}`",
            f"- Node Count: {export['node_count']}",
            f"- Edge Count: {export['edge_count']}",
            "",
            "## Nodes",
        ]
        for node in export["nodes"]:
            lines.append(f"- `{node['path']}`: {node['title']}")
        if not export["nodes"]:
            lines.append("- none")
        lines.extend(["", "## Edges"])
        for edge in export["edges"]:
            lines.append(f"- `{edge['source']}` -> `{edge['target']}` ({edge['label']})")
        if not export["edges"]:
            lines.append("- none")
        return "\n".join(lines) + "\n"
