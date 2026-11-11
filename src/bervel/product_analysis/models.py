"""Transport-independent inputs and results for shared product rules."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import NotRequired, TypedDict

class Demand(TypedDict):
    id: str
    name: str | None
    quantity: str | None
    unit: str | None
    characteristics: list[dict]

class StockRow(TypedDict):
    row: NotRequired[int]
    sku: str
    product: str
    free_quantity: str | None
    unit: str
    unit_cost: NotRequired[str | None]
    currency: NotRequired[str | None]
    as_of: NotRequired[str | None]
    attributes: dict[str, str]

class StockData(TypedDict):
    data_kind: NotRequired[str]
    rows: list[StockRow]

class CandidateSelection(TypedDict):
    sku: str
    evidence: dict[str, dict[str, str]]

@dataclass
class PreparedCandidates:
    requirements: dict[str, dict]
    candidates: list[dict]
    review_candidates: list[dict]
    uncertain: bool

@dataclass
class MatchResult:
    by_sku: dict[str, list[dict[str, str]]]
    uncertain: bool
    reason: str | None = None

@dataclass
class CoverageResult:
    percent: Decimal | None
    incomplete: bool

@dataclass
class AllocationResult:
    allocations: dict[str, dict[str, tuple[Decimal, Decimal]]]
    covered: dict[str, Decimal]
    covered_in_pieces: dict[str, Decimal]
    demand_scales: dict
    unconfirmed: set[str]
    coverage: CoverageResult

class ProductionJob(TypedDict):
    position_id: str
    resource_group_id: str
    release: date
    deadline: date
    work: Decimal
    alternatives: NotRequired[bool]
    confirmed: NotRequired[bool]

class ResourceCalendar(TypedDict, total=False):
    calendar: list[int]
    default_daily_capacity: str
    free_capacity_by_day: dict[str, str]

@dataclass
class ScheduleResult:
    schedule: list[dict] = field(default_factory=list)
    ready_at: dict[str, date] = field(default_factory=dict)
    unplaced: dict[str, Decimal] = field(default_factory=dict)
    unknown: set[str] = field(default_factory=set)
    original_capacity: dict[tuple[str, date], Decimal] = field(default_factory=dict)
    remaining: dict[tuple[str, date], Decimal] = field(default_factory=dict)
