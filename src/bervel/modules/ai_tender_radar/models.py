from datetime import datetime
from typing import Literal, NotRequired, TypedDict
from uuid import UUID

USER_STOPPED = 'USER_STOPPED'


class StockRow(TypedDict):
    row: int
    sku: str
    product: str
    free_quantity: str | None
    unit: str
    unit_cost: str | None
    currency: str | None
    as_of: str | None
    attributes: dict[str, str]


class StockData(TypedDict):
    data_kind: str
    rows: list[StockRow]


class SourceLimitation(TypedDict):
    code: str
    lot: NotRequired[int]
    position_id: NotRequired[str]


class TenderPosition(TypedDict):
    id: str
    lot: int
    customer: int | None
    name: str | None
    quantity: str | None
    unit: str | None
    item_price: str | None
    position_price: str | None
    currency: str | None
    characteristics: list[dict]


class TenderData(TypedDict):
    id: str
    title: str
    number: str | None
    link: str | None
    positions: list[TenderPosition]
    limitations: list[SourceLimitation]
    simulated: NotRequired[bool]
    terms: NotRequired[dict]
    source_card: NotRequired[dict]
    documents: NotRequired[list[dict]]
    initial_sum: NotRequired[dict | None]
    application_deadline: NotRequired[str | None]
    lot_count: NotRequired[int]
    source_stage_flags: NotRequired[dict]


class ArchiveSource(TypedDict):
    artifact_id: str
    filename: str
    sha256: str


class ArchivedTender(TypedDict):
    schema_version: int
    tender: TenderData
    source: ArchiveSource


class StockSummary(TypedDict):
    filename: str
    sha256: str
    row_count: int
    data_kind: str
    as_of_from: str | None
    as_of_to: str | None


class TenderSummary(TypedDict):
    id: str
    title: str
    number: str | None
    link: str | None
    position_count: int
    limitations: list[SourceLimitation]
    simulated: NotRequired[bool]


class JobError(TypedDict):
    code: str
    message: str


JobState = Literal['waiting', 'queued', 'running', 'loaded', 'no_match', 'failed']
BenefitScope = Literal['full', 'confirmed']


class AnalysisResult(TypedDict):
    version: int
    model: str | None
    model_error: JobError | None
    mock: bool
    status: Literal['complete', 'insufficient_data', 'partial']
    coverage_percent: str | None
    coverage_reason: str | None
    benefit_percent: str | None
    benefit_clamped: bool
    economics: dict
    positions: list[dict]


class JobView(TypedDict):
    job_id: UUID
    batch_id: UUID | None
    stock_artifact_id: UUID
    search_phrase: str
    state: JobState
    created_at: datetime
    updated_at: datetime
    stock: StockSummary | None
    tender: TenderSummary | None
    analysis: AnalysisResult | None
    error: JobError | None
    operation: NotRequired[dict | None]


class TenderCard(TypedDict):
    job_id: UUID
    stock_artifact_id: UUID
    updated_at: datetime
    tender: TenderSummary
    status: Literal['complete', 'insufficient_data', 'partial']
    coverage_percent: str | None
    position_coverage_percent: str | None
    benefit_percent: str | None
    benefit_reason: str | None
    benefit_scope: NotRequired[BenefitScope | None]
    mock: bool


class CardPage(TypedDict):
    cards: list[TenderCard]
    next_offset: int | None


CardSort = Literal['coverage', 'benefit']


class ClaimedJob(TypedDict):
    job_id: UUID
    stock_artifact_id: UUID
    stock_sha256: str
    search_phrase: str
    actor_id: str
    data_scope: str
    target_kontur_id: str | None
    snapshot: ArchivedTender | None
    operation: NotRequired[dict | None]
