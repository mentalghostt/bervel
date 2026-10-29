"""Operation results and import data; no HTTP or database types."""
from typing import Literal, NotRequired, TypedDict
from bervel.core.document_models import DocumentSnapshot, DocumentSource


class TenderOrigin(TypedDict):
    sheet: str | None
    row: int


class TenderRecord(TypedDict):
    id: str
    number: str
    title: str
    buyer: str
    price: str | None
    currency: str
    published_at: str | None
    deadline_at: str | None
    source_url: str
    source_observed_at: str | None
    stage: Literal['open', 'closed', 'cancelled', 'unknown']
    stage_original: str
    region: str
    description: str
    provenance: str
    demo: bool
    origin: TenderOrigin
    identity_conflict: bool
    origins: NotRequired[list[TenderOrigin]]


class TenderImport(TypedDict):
    schema_id: str
    schema_version: str
    records: list[TenderRecord]
    skipped_rows: int
    duplicate_rows: int
    warnings: list[str]


class RadarBatch(TypedDict):
    batch_id: str
    artifact_id: str
    filename: str
    created_at: str
    count: int
    warnings: list[str]
    skipped_rows: int
    duplicate_rows: int


class RadarAssessment(TypedDict):
    id: str
    fit: Literal['relevant', 'uncertain', 'irrelevant']
    reason: str
    evidence_indexes: list[int]
    product_group: str | None
    tender_quote: str | None
    profile_quote: str | None
    risks: list[str]
    next_steps: list[str]
    positions: list['RequestedPosition']
    positions_complete: bool


class RequestedPosition(TypedDict):
    sku: str | None
    exact_match: bool
    quantity: str | None
    unit: str | None
    quote: str


class StockItem(TypedDict):
    sku: str
    product: str
    free_quantity: str | None
    unit: str
    specs: dict[str,str]
    row: int
    as_of: str | None


class StockData(TypedDict):
    kind: Literal['mock','reported']
    items: list[StockItem]


class RadarReport(TypedDict):
    schema_id: str
    schema_version: str
    as_of: str
    counts: dict[str, int]
    items: list[dict]
    sources: list[DocumentSource]
    notice: str
    batch: NotRequired[dict[str, str | int]]
    selection: NotRequired['RadarSelection']
    stock_source: NotRequired[DocumentSnapshot | None]
    stock_kind: NotRequired[Literal['mock','reported'] | None]
    funnel: NotRequired[dict[str,int]]
    economics_source: NotRequired[DocumentSnapshot]
    economics_kind: NotRequired[Literal['mock','reported']]
    analysis_complete: NotRequired[bool]
    coverage: NotRequired[dict]
    portion_errors: NotRequired[list[dict]]


class RadarSelection(TypedDict):
    stock_source: DocumentSnapshot | None
    max_shortage_percent: str
    procurement_source: NotRequired[DocumentSnapshot]
    economics_source: NotRequired[DocumentSnapshot]
    pipeline_version: NotRequired[str]
    filters: NotRequired[dict]


class RadarRunSummary(TypedDict):
    run_id: str
    batch_id: str
    created_at: str
    focus: str
    state: Literal['queued', 'running', 'completed', 'failed']


class RadarRun(RadarRunSummary):
    sources_snapshot: list[DocumentSnapshot]
    report: RadarReport | None
    model: str | None
    error: str | None
    selection: RadarSelection
