"""Serializable contracts owned by the presales module."""
from typing import TypedDict


class PresalesSettings(TypedDict):
    target_margin_percent: str
    special_min_margin_percent: str
    seller_contacts: str
    offer_terms: str
