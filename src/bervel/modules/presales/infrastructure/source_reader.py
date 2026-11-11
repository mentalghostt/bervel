"""Adapt an existing original Artifact to complete presales evidence."""
from pathlib import Path
from bervel.core.models import Artifact
from bervel.core.infrastructure.source_sections import iter_source_sections


class RequestSourceReader:
    def __init__(self, workspace: Path | None = None):
        self.workspace = workspace

    def read(self, artifact: Artifact) -> list[dict]:
        return list(iter_source_sections(artifact.content, artifact.metadata['filename'],
            artifact_id=str(artifact.ref.artifact_id), workspace=self.workspace))
