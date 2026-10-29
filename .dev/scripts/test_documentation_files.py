"""Keep third-party and generated Markdown out of project documentation checks."""
import tempfile
import unittest
from pathlib import Path

from check_project_materials import documentation_files


class DocumentationFileTests(unittest.TestCase):
    def test_nested_project_documents_are_retained(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            for relative in ("AGENTS.md", "docs/quality/report.md", "experiments/example/README.md"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Project document", encoding="utf-8")
            self.assertEqual(
                {str(path.relative_to(root)).replace("\\", "/") for path in documentation_files(root)},
                {"AGENTS.md", "docs/quality/report.md", "experiments/example/README.md"},
            )

    def test_dependency_and_generated_documents_are_not_project_evidence(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            for relative in ("node_modules/package/README.md", "experiments/example/node_modules/package/README.md",
                             ".venv/package/README.md", "local/cache/README.md", "docs/report.md"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("[link](not-present.md)", encoding="utf-8")
            self.assertEqual(
                [str(path.relative_to(root)).replace("\\", "/") for path in documentation_files(root)],
                ["docs/report.md"],
            )


if __name__ == "__main__":
    unittest.main()
