"""External operations required by the presales workflow."""
from collections.abc import Iterable
from typing import Protocol

from bervel.core.models import Artifact


class SourceReader(Protocol):
    def read(self, artifact: Artifact) -> Iterable[dict]: ...


class OfferRenderer(Protocol):
    def render_pdf(self, offer: dict) -> bytes: ...
    def render_docx(self, offer: dict) -> bytes: ...
