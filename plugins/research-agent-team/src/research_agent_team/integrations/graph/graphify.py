from __future__ import annotations

import json
import posixpath
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Tuple


LINK_PATTERN = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")


class GraphifyGraphAdapter:
    adapter_id = "graphify"

    def extract(self, *, root: Path, document_paths: List[Path]) -> Dict[str, Any]:
        known_paths = {
            self._relative_path(root, path): path
            for path in document_paths
        }
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        seen_edges: set[Tuple[str, str, str]] = set()

        for path in sorted(document_paths):
            relative_path = self._relative_path(root, path)
            text = path.read_text(encoding="utf-8")
            title = self._title(text, path.stem)
            nodes.append(
                {
                    "id": relative_path,
                    "label": title,
                    "file_type": "document",
                    "source_file": relative_path,
                    "source_location": None,
                    "path": relative_path,
                    "title": title,
                }
            )
            for label, target in LINK_PATTERN.findall(text):
                resolved = self._resolve_link(relative_path, target, known_paths)
                if resolved is None:
                    continue
                edge_key = (relative_path, resolved, "markdown_link")
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                edges.append(
                    {
                        "source": relative_path,
                        "target": resolved,
                        "relation": "markdown_link",
                        "confidence": "EXTRACTED",
                        "confidence_score": 1.0,
                        "source_file": relative_path,
                        "source_location": None,
                        "weight": 1.0,
                        "label": label.strip(),
                    }
                )

        return {"nodes": nodes, "edges": edges, "hyperedges": [], "input_tokens": 0, "output_tokens": 0}

    def build(self, *, root: Path, document_paths: List[Path], generated_at: str, mode: str) -> tuple[Dict[str, Any], str]:
        extraction = self.extract(root=root, document_paths=document_paths)
        if not extraction["nodes"]:
            return self._empty_export(generated_at=generated_at, mode=mode), self._empty_report(mode=mode)

        graphify = self._load_graphify()
        graph = graphify["build_from_json"](extraction, directed=True)
        communities = graphify["cluster"](graph)
        cohesion = graphify["score_all"](graph, communities)
        labels = {community_id: f"Community {community_id}" for community_id in communities}
        gods = graphify["god_nodes"](graph)
        surprises = graphify["surprising_connections"](graph, communities)
        questions = graphify["suggest_questions"](graph, communities, labels)
        detection = self._detection(root=root, document_paths=document_paths)
        tokens = {"input": 0, "output": 0}
        graphify_report = graphify["generate"](
            graph,
            communities,
            cohesion,
            labels,
            gods,
            surprises,
            detection,
            tokens,
            "shared/wiki",
            suggested_questions=questions,
        )
        export = self._export_graphify_json(
            graphify["to_json"],
            graph,
            communities,
            generated_at=generated_at,
            mode=mode,
            document_paths=[node["path"] for node in extraction["nodes"]],
        )
        return export, self._report(graphify_report, export)

    def _load_graphify(self) -> Dict[str, Callable[..., Any]]:
        try:
            from graphify.analyze import god_nodes, suggest_questions, surprising_connections
            from graphify.build import build_from_json
            from graphify.cluster import cluster, score_all
            from graphify.export import to_json
            from graphify.report import generate
        except ImportError as exc:
            raise RuntimeError("graphifyy is not installed; install graphifyy or configure adapter local_file") from exc

        return {
            "build_from_json": build_from_json,
            "cluster": cluster,
            "score_all": score_all,
            "god_nodes": god_nodes,
            "surprising_connections": surprising_connections,
            "suggest_questions": suggest_questions,
            "generate": generate,
            "to_json": to_json,
        }

    def _export_graphify_json(
        self,
        to_json: Callable[..., Any],
        graph: Any,
        communities: Dict[int, List[str]],
        *,
        generated_at: str,
        mode: str,
        document_paths: List[str],
    ) -> Dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "graph.json"
            try:
                written = to_json(graph, communities, str(output_path), force=True)
            except TypeError:
                written = to_json(graph, communities, str(output_path))
            if written is False:
                raise RuntimeError("graphify refused to write graph JSON")
            graph_data = json.loads(output_path.read_text(encoding="utf-8"))

        nodes = list(graph_data.get("nodes", []))
        edges = list(graph_data.get("links", graph_data.get("edges", [])))
        export = {
            "adapter_id": self.adapter_id,
            "mode": mode,
            "generated_at": generated_at,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes": nodes,
            "edges": edges,
            "links": edges,
            "hyperedges": graph_data.get("hyperedges", []),
            "provenance": {"document_paths": document_paths},
        }
        for key, value in graph_data.items():
            if key not in {"nodes", "links", "edges", "hyperedges"}:
                export[key] = value
        return export

    def _detection(self, *, root: Path, document_paths: List[Path]) -> Dict[str, Any]:
        total_words = 0
        relative_paths: List[str] = []
        for path in document_paths:
            relative_paths.append(self._relative_path(root, path))
            total_words += len(path.read_text(encoding="utf-8").split())
        return {
            "files": {"code": [], "document": relative_paths, "paper": [], "image": [], "video": []},
            "total_files": len(relative_paths),
            "total_words": total_words,
            "needs_graph": True,
            "warning": None,
            "skipped_sensitive": [],
        }

    def _relative_path(self, root: Path, path: Path) -> str:
        return path.resolve().relative_to(root.resolve()).as_posix()

    def _title(self, text: str, fallback: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip()
                if title:
                    return title
        return fallback

    def _resolve_link(self, source_relative_path: str, raw_target: str, known_paths: Dict[str, Path]) -> str | None:
        target = raw_target.strip()
        if (
            not target
            or target.startswith("#")
            or target.startswith("/")
            or "://" in target
            or target.startswith("mailto:")
        ):
            return None
        target_path = target.split("#", 1)[0].strip()
        if not target_path:
            return None
        resolved = PurePosixPath(
            posixpath.normpath(str(PurePosixPath(source_relative_path).parent / PurePosixPath(target_path)))
        ).as_posix()
        if resolved not in known_paths:
            return None
        return resolved

    def _empty_export(self, *, generated_at: str, mode: str) -> Dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "mode": mode,
            "generated_at": generated_at,
            "node_count": 0,
            "edge_count": 0,
            "nodes": [],
            "edges": [],
            "links": [],
            "hyperedges": [],
            "provenance": {"document_paths": []},
        }

    def _empty_report(self, *, mode: str) -> str:
        return "\n".join(
            [
                "# Graphify Graph Report",
                "",
                f"- Adapter: `{self.adapter_id}`",
                f"- Mode: `{mode}`",
                "- Node Count: 0",
                "- Edge Count: 0",
                "",
                "No project wiki documents were available for graph construction.",
                "",
            ]
        )

    def _report(self, graphify_report: str, export: Dict[str, Any]) -> str:
        prelude = "\n".join(
            [
                "# Graphify Graph Report",
                "",
                f"- Adapter: `{self.adapter_id}`",
                f"- Mode: `{export['mode']}`",
                f"- Node Count: {export['node_count']}",
                f"- Edge Count: {export['edge_count']}",
                "",
            ]
        )
        return prelude + graphify_report.lstrip()
