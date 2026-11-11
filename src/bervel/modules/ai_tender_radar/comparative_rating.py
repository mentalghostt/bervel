"""Compact, saved model ratings relative to the four metrics of an entire run."""
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import hashlib
import json

from bervel.core.errors import CoreError


PROMPT_VERSION = 'run-relative-rating-v1'
MAX_BATCH = 100
MAX_PROMPT_BYTES = 64 * 1024
INSTRUCTIONS = (
    'Оцени привлекательность каждой карточки относительно ВСЕГО прогона только по четырём агрегатам: '
    'стоимость тендера, соответствие продукции, покрытие склада, маржинальность. '
    'Верни ratings с каждым входным id ровно один раз и целым score от 0 до 100. '
    'Соответствие и покрытие показывают близость заказа предприятию, маржа — относительную выгоду, '
    'стоимость — масштаб заказа. Применяй одну сравнительную шкалу ко всем пакетам: cohort содержит '
    'распределения всего прогона, percentile — положение среди известных значений этого показателя '
    '(0 минимум, 100 максимум, 50 при равенстве). Это ориентиры, не готовый итоговый балл. '
    'Сильные относительные лидеры заслуживают 80–100 даже когда лучшее покрытие всего 5–6%. '
    'Не умножай балл на абсолютный процент покрытия и не считай низкие проценты автоматическим нулём. '
    'Одинаковые четыре агрегата оценивай одинаково независимо от id и пакета. '
    'null означает неизвестное, а не ноль; incomplete означает неполноту подтверждения. '
    'Не придумывай отсутствующие факты. Если все четыре показателя неизвестны, ставь нейтральные 50. '
    'Маржа scope=confirmed относится только к подтверждённой части, не ко всему заказу. '
    'Денежные суммы разных валют не сравнивай напрямую; их percentile рассчитан внутри валюты. '
    'Оценка показывает сравнительный интерес и не является решением об участии. '
    'Не используй внешние инструменты или какие-либо иные критерии.\nВХОД\n'
)
OUTPUT_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['ratings'],
    'properties': {'ratings': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['id', 'score'], 'properties': {'id': {'type': 'string'},
            'score': {'type': 'integer', 'minimum': 0, 'maximum': 100}}}}}}


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _rounded(value, places: int, *, percentage: bool = False, signed: bool = False) -> str | None:
    try:
        number = Decimal(str(value))
        if (not number.is_finite() or abs(number.adjusted()) > 24
                or (not signed and number < 0) or (percentage and number > 100)):
            return None
        with localcontext() as context:
            context.prec = 60
            rounded = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        return format(abs(rounded) if rounded == 0 else rounded, 'f')
    except (InvalidOperation, ValueError, TypeError):
        return None


def build_snapshot(cards: list[dict]) -> dict:
    """Normalize only displayed metrics; source snapshots and decisions are untouched."""
    rows, identifiers = [], set()
    for card in cards:
        identifier = card.get('analysis_id')
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 128 or identifier in identifiers:
            raise CoreError('VALIDATION_ERROR', 'Для сравнительной оценки нужны уникальные ID карточек.')
        identifiers.add(identifier)
        metrics, opportunity = card.get('metrics') or {}, card.get('opportunity') or {}
        currency = metrics.get('currency')
        currency = currency.upper() if isinstance(currency, str) and len(currency) == 3 and currency.isascii() and currency.isalpha() else None
        rows.append({'id': identifier,
            'price': {'amount': _rounded(metrics.get('price'), 2), 'currency': currency},
            'product_match': {'percent': _rounded(metrics.get('catalog_match_percent'), 4, percentage=True),
                'incomplete': bool(metrics.get('catalog_match_incomplete'))},
            'stock_coverage': {'percent': _rounded(metrics.get('stock_coverage_percent'), 4, percentage=True),
                'incomplete': bool(metrics.get('stock_coverage_incomplete'))},
            'margin': {'percent': _rounded(opportunity.get('margin_percent'), 4, signed=True),
                'scope': opportunity.get('basis') if opportunity.get('basis') in {'full', 'confirmed'} else 'unknown'}})
    rows.sort(key=lambda row: row['id'])
    fingerprint = hashlib.sha256(json_text({'prompt_version': PROMPT_VERSION, 'cards': rows}).encode()).hexdigest()
    return {'prompt_version': PROMPT_VERSION, 'fingerprint': fingerprint, 'cohort_size': len(rows), 'cards': rows}


def _distribution(values: list[Decimal], total: int) -> dict:
    return {'known': len(values), 'unknown': total - len(values),
        'quantiles': [format(values[round((len(values) - 1) * point / 4)], 'f') for point in range(5)] if values else []}


def _percentile(values: list[Decimal], value: str | None) -> str | None:
    if value is None:
        return None
    number = Decimal(value)
    rank = (Decimal(bisect_left(values, number) + bisect_right(values, number) - 1)
            / (2 * (len(values) - 1)) * 100) if len(values) > 1 else Decimal(50)
    return format(rank.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP).normalize(), 'f')


def build_batches(snapshot: dict) -> list[dict]:
    """Every packet shares bounded global distributions and global metric ranks."""
    rows = deepcopy(snapshot['cards'])
    values = {key: sorted(Decimal(row[key]['percent']) for row in rows if row[key]['percent'] is not None)
              for key in ('product_match', 'stock_coverage', 'margin')}
    currencies = defaultdict(list)
    for row in rows:
        if row['price']['amount'] is not None and row['price']['currency'] is not None:
            currencies[row['price']['currency']].append(Decimal(row['price']['amount']))
    for amounts in currencies.values():
        amounts.sort()
    main_currencies = sorted(currencies, key=lambda key: (-len(currencies[key]), key))[:8]
    cohort = {'size': len(rows), **{key: _distribution(numbers, len(rows)) for key, numbers in values.items()},
        'price': {'known': sum(map(len, currencies.values())), 'currency_count': len(currencies),
            'currencies': {key: _distribution(currencies[key], len(currencies[key])) for key in main_currencies},
            'other_currency_count': max(0, len(currencies) - len(main_currencies))}}
    cohort['margin']['scopes'] = dict(Counter(row['margin']['scope'] for row in rows))
    for row in rows:
        for key, numbers in values.items():
            row[key]['percentile'] = _percentile(numbers, row[key]['percent'])
        price = row['price']
        price['percentile'] = _percentile(currencies[price['currency']], price['amount']) if price['currency'] in currencies else None
    batches, batch = [], []
    for row in rows:
        candidate = {'cohort': cohort, 'cards': [*batch, row]}
        if batch and (len(batch) >= MAX_BATCH or len((INSTRUCTIONS + json_text(candidate)).encode()) > MAX_PROMPT_BYTES):
            batches.append({'cohort': cohort, 'cards': batch})
            batch = []
        batch.append(row)
        if len((INSTRUCTIONS + json_text({'cohort': cohort, 'cards': batch})).encode()) > MAX_PROMPT_BYTES:
            raise CoreError('MODEL_CONTEXT_LIMIT', 'Карточка превышает допустимый контекст сравнительной оценки.')
    if batch:
        batches.append({'cohort': cohort, 'cards': batch})
    return batches


def _validate(answer: str | None, batch: dict) -> list[dict]:
    try:
        value = json.loads(answer)
        if not isinstance(value, dict) or set(value) != {'ratings'} or not isinstance(value['ratings'], list):
            raise ValueError
        expected = {row['id'] for row in batch['cards']}
        for entry in value['ratings']:
            if (not isinstance(entry, dict) or set(entry) != {'id', 'score'}
                    or type(entry['score']) is not int or not 0 <= entry['score'] <= 100):
                raise ValueError
            expected.remove(entry['id'])
        if expected:
            raise ValueError
        return value['ratings']
    except (TypeError, ValueError, KeyError) as error:
        raise CoreError('MODEL_INVALID_OUTPUT', 'Не удалось проверить сравнительные оценки всех карточек.') from error


def initial_result(snapshot: dict, request_id: str) -> dict:
    return {key: snapshot[key] for key in ('prompt_version', 'fingerprint', 'cohort_size')} | {
        'status': 'running', 'request_id': request_id, 'started_at': datetime.now(timezone.utc).isoformat(),
        'model': None, 'ratings': [], 'completed_requests': [], 'pending_request_id': None,
        'snapshot': deepcopy(snapshot)}


def rate_snapshot(snapshot: dict, model, request_id: str, *, heartbeat, save, checkpoint: dict | None = None) -> dict:
    result = deepcopy(checkpoint) if checkpoint is not None else initial_result(snapshot, request_id)
    if checkpoint is not None:
        if (checkpoint.get('snapshot') != snapshot or checkpoint.get('request_id') != request_id
                or any(checkpoint.get(key) != snapshot[key] for key in ('prompt_version', 'fingerprint', 'cohort_size'))):
            raise CoreError('VALIDATION_ERROR', 'Сохранённый рейтинг относится к другому набору данных.')
        if result.get('status') == 'complete':
            return result
        result.update(status='running', pending_request_id=None)
        result.pop('error', None)
    save(deepcopy(result))
    try:
        for index, batch in enumerate(build_batches(snapshot)):
            heartbeat()
            batch_id = f'{request_id}-rating-{index}'
            if batch_id in result['completed_requests']:
                identifiers = {row['id'] for row in batch['cards']}
                _validate(json_text({'ratings': [row for row in result['ratings'] if row['id'] in identifiers]}), batch)
                continue
            result['pending_request_id'] = batch_id
            save(deepcopy(result))  # A process crash after this point has an unknown remote outcome.
            response = model.generate(result['pending_request_id'], INSTRUCTIONS + json_text(batch),
                output_schema=OUTPUT_SCHEMA, web_search=False, timeout_seconds=120)
            ratings = _validate(response.get('answer') if isinstance(response, dict) else None, batch)
            result['ratings'].extend(ratings)
            result['model'] = response.get('model')
            result['completed_requests'].append(result['pending_request_id'])
            result['pending_request_id'] = None
            save(deepcopy(result))
        result.update(status='complete', completed_at=datetime.now(timezone.utc).isoformat())
    except CoreError as error:
        known_failure = error.code in {'MODEL_INVALID_OUTPUT', 'MODEL_CONTEXT_LIMIT', 'MODEL_UNCONFIGURED',
            'MODEL_AUTH_ERROR', 'MODEL_RATE_LIMITED', 'MODEL_QUOTA_EXCEEDED', 'USER_STOPPED'}
        result.update(status='failed' if known_failure else 'unknown', error={'code': error.code, 'message': error.message})
    except Exception:
        result.update(status='unknown', error={'code': 'RATING_INTERRUPTED',
            'message': 'Исход запроса сравнительной оценки неизвестен. Автоматического повтора нет.'})
    save(deepcopy(result))
    return result


def matching_report(snapshot: dict, reports: list[dict]) -> dict | None:
    matching = [entry for entry in reports if entry['data'].get('kind') == 'rating'
        and all(entry['data'].get('result', {}).get(key) == snapshot[key] for key in ('prompt_version', 'fingerprint'))]
    return next((entry for entry in matching if entry['data']['result']['status'] == 'complete'), matching[-1] if matching else None)


def apply_rating(cards: list[dict], reports: list[dict], *, rating_version: str | None = None) -> None:
    """Overlay a saved rating only. This path never calls models or writes history."""
    from bervel.modules.ai_tender_radar.individual_rating import PROMPT_VERSION, saved_rating
    legacy = []
    for card in cards:
        if card.get('individual_rating') or card.get('rating_version') == PROMPT_VERSION or rating_version == PROMPT_VERSION:
            card['rating'] = dict(saved_rating(card), prompt_version=PROMPT_VERSION)
        else:
            legacy.append(card)
    if not legacy:
        return
    entry = matching_report(build_snapshot(cards), reports)
    status = 'stale' if any(entry['data'].get('kind') == 'rating' for entry in reports) else 'pending'
    ratings = {}
    if entry:
        result = entry['data']['result']
        status = result['status']
        if status == 'running' and entry.get('job_state') in {'failed', 'loaded', 'no_match'}:
            status = 'unknown'
        if status == 'complete':
            ratings = {row['id']: row['score'] for row in result['ratings']}
    for card in legacy:
        card['rating'] = {'status': status, 'score': ratings.get(card['analysis_id'])}
