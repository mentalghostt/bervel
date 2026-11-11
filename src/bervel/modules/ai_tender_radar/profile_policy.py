"""Pure rules for the current tender base, independent of analysis completeness."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

from bervel.modules.ai_tender_radar.result_metrics import result_metrics


MOSCOW = timezone(timedelta(hours=3))
_LAWS = {1: 'fz44', 2: 'fz223', 3: 'commercial_small', 5: 'pp615',
         8: 'commercial', 9: 'small'}
_TYPE_NAMES = {'fz44': 'fz44', '44-фз': 'fz44', 'fz223': 'fz223', '223-фз': 'fz223',
               'pp615': 'pp615', '615 пп рф': 'pp615', 'commercial': 'commercial',
               'коммерческие': 'commercial', 'small': 'small', 'малые закупки': 'small',
               'commercial_small': 'commercial_small', 'коммерческие и малые закупки': 'commercial_small',
               'cis': 'cis', 'снг': 'cis', 'закупки снг': 'cis'}


def search_fingerprint(keywords: list[str]) -> str:
    words = sorted({' '.join(word.split()).casefold() for word in keywords if word.strip()})
    payload = json.dumps({'keywords': words, 'stages': [1, 4]}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _metadata(card: dict) -> dict:
    tender, brief = card.get('tender') or {}, card.get('brief') or {}
    metadata = dict(tender.get('source_card') or {})
    metadata.update(tender.get('source_stage_flags') or {})
    if 'ApplicationDeadline' not in metadata and tender.get('application_deadline'):
        metadata['ApplicationDeadline'] = tender['application_deadline']
    metadata.update(brief)
    # An explicit fresh negative flag supersedes a cancellation in an older detail.
    if brief.get('IsCancelled') is False:
        metadata.pop('CancelReason', None)
    return metadata


def card_actuality(card: dict, now: datetime | None = None) -> dict:
    stamp = now or datetime.now(timezone.utc)
    metadata = _metadata(card)
    reason = None
    deadline = metadata.get('ApplicationDeadline')
    if isinstance(deadline, str):
        try:
            cutoff = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
        except ValueError:
            cutoff = None
        # Kontur's unqualified source date is Moscow local time.
        if cutoff is not None and cutoff.replace(tzinfo=cutoff.tzinfo or MOSCOW) <= stamp:
            reason = 'deadline_expired'
    if reason is None:
        status = metadata.get('PurchaseStatus', metadata.get('Stage'))
        if (card.get('source_closed') or metadata.get('IsCancelled') is True or metadata.get('IsAbandoned') is True
                or metadata.get('CancelReason') or type(status) is int and status not in {1, 4}
                or isinstance(status, str) and status.casefold() in {
                    'closed', 'finished', 'commission', 'cancelled', 'abandoned'}):
            reason = 'stage_closed'
        elif card.get('source_unavailable'):
            reason = 'source_unavailable'
        elif card.get('source_missing'):
            reason = 'source_missing'
    return {'actuality': 'inactive' if reason else 'active', 'inactivity_reason': reason}


def trade_type(card: dict) -> str:
    metadata = _metadata(card)
    value = metadata.get('TypeOfTrading', metadata.get('Law'))
    if type(value) is int:
        return _LAWS.get(value, 'unknown')
    if isinstance(value, str):
        value = ' '.join(value.split()).casefold()
        return _LAWS.get(int(value), 'unknown') if value.isascii() and value.isdigit() else _TYPE_NAMES.get(value, 'unknown')
    return 'unknown'


def profile_metrics(card: dict, now: datetime | None = None) -> dict:
    current = deepcopy(card)
    tender = current.setdefault('tender', None) or {}
    current['tender'] = tender
    source = _metadata(card)
    brief = card.get('brief') or {}
    if 'MaxPrice' in brief:
        source['InitialSum'] = {'Price': brief['MaxPrice'], 'Currency': brief.get('Currency')}
    tender['source_card'] = source
    metrics = result_metrics(current, now=now)
    projection = card.get('metrics_projection')
    if projection:
        from bervel.modules.ai_tender_radar.metrics_refresh import analysis_fingerprint
        if projection.get('source_analysis_fingerprint') == analysis_fingerprint(card):
            metrics.update(stock_coverage_percent=projection['coverage']['percent'],
                           stock_coverage_incomplete=projection['coverage']['incomplete'])
    return dict(metrics, **card_actuality(card, now), trade_type=trade_type(card))
