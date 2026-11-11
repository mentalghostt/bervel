"""Source identities shared by selection policy and transport adapters."""
from datetime import datetime, timezone
from typing import Literal

BASE_URL = 'https://api-zakupki.kontur.ru'
SIMULATOR_KEY = 'bervel-kontur-simulator'

PurchaseStage = Literal['open', 'commission', 'finished', 'planned', 'cancelled']
PURCHASE_STATUS: dict[PurchaseStage, int] = {
    'open': 1, 'commission': 2, 'finished': 3, 'planned': 4, 'cancelled': 5,
}


def brief_is_closed(item: dict, purchase_stages: list[str] | None) -> bool:
    stages = purchase_stages or ['open']
    flagged = next((stage for flag, stage in (
        ('IsCancelled', 'cancelled'), ('IsAbandoned', 'unsuccessful'), ('IsPlanning', 'planned'),
    ) if item.get(flag) is True), None)
    if flagged is not None and flagged not in stages:
        return True
    deadline = item.get('ApplicationDeadline')
    if set(stages) == {'open'} and isinstance(deadline, str):
        try:
            return datetime.fromisoformat(deadline) < datetime.now(timezone.utc)
        except (ValueError, TypeError):
            pass
    return False
