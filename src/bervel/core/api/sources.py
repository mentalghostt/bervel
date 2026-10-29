"""Shared HTTP representation of evidence from uploaded documents."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel


class SourceResponse(BaseModel):
    artifact_id: UUID
    filename: str
    created_at: datetime
    row_start: int
    row_end: int
    total_rows: int
    excerpt: str
    truncated: bool
    unit: Literal['row', 'line', 'page', 'image', 'metadata'] = 'row'
    status: Literal['ready', 'partial', 'metadata_only'] = 'ready'
    media_type: str = 'text/csv'
    image_attached: bool = False


