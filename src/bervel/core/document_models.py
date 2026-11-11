"""Document evidence exchanged with module operations, independent of HTTP/SQL."""
from typing import Literal, NotRequired, TypedDict


class SourceSection(TypedDict):
    """One fully visited source unit, independent of bounded search indexes."""
    section_id: str
    artifact_id: str
    filename: str
    text: str
    unit: Literal['row', 'page', 'line', 'image']
    location: dict
    complete: bool
    issues: list[str]


class DocumentSnapshot(TypedDict):
    artifact_id: str
    filename: str
    created_at: str
    media_type: NotRequired[str]
    size: NotRequired[int]


class DocumentSource(DocumentSnapshot):
    row_start: int
    row_end: int
    total_rows: int
    excerpt: str
    truncated: bool
    unit: NotRequired[Literal['row', 'line', 'page', 'image', 'metadata']]
    status: NotRequired[Literal['ready', 'partial', 'metadata_only']]
    image_attached: NotRequired[bool]
