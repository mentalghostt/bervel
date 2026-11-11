"""Read a saved Kontur card or capture using the live source normalizer."""
from decimal import Decimal
import json
from urllib.parse import urlsplit

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.infrastructure.kontur import normalize_tender
from bervel.modules.ai_tender_radar.models import TenderData


def _decode(content: bytes | str) -> object:
    try:
        text = content.decode('utf-8-sig') if isinstance(content, bytes) else content
        return json.loads(text, parse_float=Decimal)
    except (ValueError, RecursionError) as error:
        raise CoreError('VALIDATION_ERROR', 'Не удалось прочитать JSON закупок.') from error


def read_archive(content: bytes) -> list[TenderData]:
    data = _decode(content)
    search_items = {}
    if isinstance(data, dict) and isinstance(data.get('responses'), list):
        cards = []
        for entry in data['responses']:
            if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
                raise CoreError('VALIDATION_ERROR', 'Неизвестный формат архива Контур.Закупок.')
            try:
                path = urlsplit(entry['path']).path
            except ValueError as error:
                raise CoreError('VALIDATION_ERROR', 'Неизвестный формат архива Контур.Закупок.') from error
            if path != '/external/v1/search' and not path.startswith('/external/v1/purchases/'):
                continue
            body = entry.get('body')
            payload = _decode(body) if isinstance(body, str) else body
            if path.startswith('/external/v1/purchases/'):
                cards.append(payload)
            elif isinstance(payload, dict) and isinstance(payload.get('Items'), list):
                for item in payload['Items']:
                    if isinstance(item, dict) and isinstance(item.get('Id'), str):
                        search_items.setdefault(item['Id'], item)
    else:
        cards = data if isinstance(data, list) else [data]
    if not cards or any(not isinstance(card, dict) or not isinstance(card.get('Id'), str) or 'Lots' not in card
                        for card in cards):
        raise CoreError('VALIDATION_ERROR', 'В JSON нужны полные карточки Контур.Закупок с лотами и составом.')
    unique = {card['Id']: card for card in cards}
    try:
        return [dict(normalize_tender(search_items.get(key, {'Id': key}), card),
                     initial_sum=card.get('InitialSum'),
                     application_deadline=card.get('ApplicationDeadline') or search_items.get(key, {}).get('ApplicationDeadline'),
                     lot_count=len(card.get('Lots') or []),
                     source_stage_flags={flag: search_items[key][flag] for flag in ('IsCancelled', 'IsAbandoned', 'IsPlanning')
                                         if flag in search_items.get(key, {})})
                for key, card in unique.items()]
    except CoreError as error:
        raise CoreError('VALIDATION_ERROR', f'Не удалось разобрать одну из карточек в JSON. {error.message}') from error
