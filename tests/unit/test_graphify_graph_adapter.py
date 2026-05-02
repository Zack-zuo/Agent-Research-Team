import tempfile
import unittest
from pathlib import Path

from research_agent_team.integrations.graph.graphify import GraphifyGraphAdapter


class GraphifyGraphAdapterTests(unittest.TestCase):
    def test_extracts_wiki_documents_as_graphify_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki = root / "shared" / "wiki"
            wiki.mkdir(parents=True)
            source = wiki / "paper-a.md"
            target = wiki / "paper-b.md"
            source.write_text(
                "\n".join(
                    [
                        "# Paper A",
                        "",
                        "See [Paper B](paper-b.md).",
                        "Also see [Paper B Section](paper-b.md#method).",
                        "Ignore [Missing](missing.md).",
                        "Ignore [External](https://example.com).",
                        "Ignore [Mail](mailto:test@example.com).",
                        "Ignore ![Image](figure.png).",
                    ]
                ),
                encoding="utf-8",
            )
            target.write_text("# Paper B\n\nEvidence.\n", encoding="utf-8")

            extraction = GraphifyGraphAdapter().extract(root=root, document_paths=[source, target])

            self.assertEqual(
                {
                    (node["id"], node["label"], node["source_file"])
                    for node in extraction["nodes"]
                },
                {
                    ("shared/wiki/paper-a.md", "Paper A", "shared/wiki/paper-a.md"),
                    ("shared/wiki/paper-b.md", "Paper B", "shared/wiki/paper-b.md"),
                },
            )
            self.assertEqual(
                [
                    {
                        "source": edge["source"],
                        "target": edge["target"],
                        "relation": edge["relation"],
                        "confidence": edge["confidence"],
                    }
                    for edge in extraction["edges"]
                ],
                [
                    {
                        "source": "shared/wiki/paper-a.md",
                        "target": "shared/wiki/paper-b.md",
                        "relation": "markdown_link",
                        "confidence": "EXTRACTED",
                    }
                ],
            )

    def test_build_preserves_markdown_link_direction_and_reciprocal_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki = root / "shared" / "wiki"
            wiki.mkdir(parents=True)
            first = wiki / "a.md"
            second = wiki / "z.md"
            first.write_text("# A\n\nBack to [Z](z.md).\n", encoding="utf-8")
            second.write_text("# Z\n\nForward to [A](a.md).\n", encoding="utf-8")

            export, _ = GraphifyGraphAdapter().build(
                root=root,
                document_paths=[second, first],
                generated_at="2026-05-02T00:00:00Z",
                mode="full",
            )

            edge_pairs = {(edge["source"], edge["target"]) for edge in export["edges"]}
            self.assertIn(("shared/wiki/a.md", "shared/wiki/z.md"), edge_pairs)
            self.assertIn(("shared/wiki/z.md", "shared/wiki/a.md"), edge_pairs)
            self.assertEqual(export["edge_count"], 2)

    def test_build_report_uses_project_relative_wiki_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki = root / "shared" / "wiki"
            wiki.mkdir(parents=True)
            page = wiki / "paper.md"
            page.write_text("# Paper\n\nEvidence.\n", encoding="utf-8")

            _, report = GraphifyGraphAdapter().build(
                root=root,
                document_paths=[page],
                generated_at="2026-05-02T00:00:00Z",
                mode="full",
            )

            self.assertIn("shared/wiki", report)
            self.assertNotIn(str(root), report)


if __name__ == "__main__":
    unittest.main()
