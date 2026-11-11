"""Serializable contracts owned by the presales module."""
from typing import Literal, NotRequired, TypedDict


SupplyStatus = Literal['full_stock', 'partial_or_manufacture', 'not_manufactured', 'special_order']
JobState = Literal['queued', 'running', 'stopped', 'failed', 'complete']


class PresalesSettings(TypedDict):
    target_margin_percent: str
    special_min_margin_percent: str
    seller_contacts: str
    offer_terms: str


class RequestLine(TypedDict):
    line_id: str
    name: str
    quantity: str | None
    unit: str | None
    requirements: list[dict]
    evidence: list[dict]
    client_unit_price: str | None
    currency: str | None


class ManagerDecisions(TypedDict, total=False):
    primary_list_id: str
    line_updates: dict[str, dict]
    approvals: dict[str, dict]
    client: str | None
    requested_deadline: str | None


class RequestReceipt(TypedDict):
    request_id: str
    revision_id: str
    job_id: str
    state: JobState


class AnalysisLine(RequestLine):
    supply_status: SupplyStatus | None
    needs_review: bool
    reasons: list[str]
    candidates: list[dict]
    selected_sku: str | None
    stock_quantity: str | None
    production_quantity: str | None
    offered_quantity: str | None
    ready_at: str | None
    unit_cost: str | None
    unit_price: str | None
    line_total: str | None
    margin_percent: str | None
    offers: list[dict]
    special_order: dict | None


class OfferDraft(TypedDict):
    number: str
    date: str
    client: str | None
    seller_contacts: str
    offer_terms: str
    lines: list[dict]
    totals: dict[str, str]
    clarifications: list[dict]
    unsupported: list[dict]


class RequestRevision(TypedDict):
    request_id: str
    revision_id: str
    revision_number: int
    evaluated_at: str
    extracted: dict
    analysis: dict
    decisions: ManagerDecisions
    offer: NotRequired[OfferDraft | None]
