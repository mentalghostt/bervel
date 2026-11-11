from collections import defaultdict
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from http.client import HTTPException
import json
import re
from time import monotonic, sleep
from threading import Lock
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.models import SourceLimitation, TenderData, TenderPosition
from bervel.modules.ai_tender_radar.source_contract import BASE_URL, PURCHASE_STATUS, SIMULATOR_KEY


MAX_RESPONSE = 10 * 1024 * 1024
SEARCH_PAGES = 3
PAGE_SIZE = 50
SEARCH_WINDOW = timedelta(days=365)
MOSCOW = timezone(timedelta(hours=3))
PURCHASE_ID = re.compile(r'[A-Za-zА-Яа-яЁё0-9._-]{1,128}')
MASK = re.compile(r'#{3,}|[░▒▓█]{2,}')
PARTICIPANT_VALUE = 'участник закупки указывает в заявке конкретное значение характеристики'


def _invalid() -> CoreError:
    return CoreError('KONTUR_INVALID_RESPONSE', 'Контур вернул данные, которые не удалось проверить.')


def verified_limit_groups(rows: list[dict]) -> dict[str, dict]:
    limits = {}
    for row in rows:
        if not isinstance(row, dict):
            raise _invalid()
        group = row.get('LimitGroup')
        available, total = row.get('AvailableRequests'), row.get('TotalLimit')
        if (not isinstance(group, str) or not group.strip() or group in limits
                or type(available) is not int or type(total) is not int or not 0 <= available <= total
                or type(row.get('IsPeriodic')) is not bool):
            raise _invalid()
        limits[group] = row
    return limits


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if '\0' in value or re.search(r'[\ud800-\udfff]', value):
        raise _invalid()
    return value.strip() or None


def _visible(value: object) -> str | None:
    text = _text(value)
    return text if text and not MASK.search(text) else None


def _array(value: object) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise _invalid()
    return value


def _number(value: object) -> str | None:
    if value is None or isinstance(value, str) and not _visible(value):
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or abs(number.as_tuple().exponent) > 30 or number.adjusted() > 29:
            raise ValueError
    except (InvalidOperation, ValueError):
        raise _invalid() from None
    return format(number, 'f')


def _currency(value: object) -> str | None:
    code = _visible(value)
    if code is None:
        return None
    normalized = {'руб.': 'RUB', 'руб': 'RUB', '₽': 'RUB'}.get(code.casefold(), code.upper())
    return normalized if re.fullmatch('[A-Z]{3}', normalized) else None


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and value not in {'.', '..'} and PURCHASE_ID.fullmatch(value) is not None


def _link(value: object) -> str | None:
    link = _visible(value)
    if link is not None:
        try:
            parsed = urlsplit(link)
            if parsed.scheme in {'https', 'http'} and parsed.hostname and parsed.username is None:
                return link
        except ValueError:
            pass
    return None


def _features(obj: dict) -> list[dict]:
    return [dict(Name=_text(feature.get('Name')), FillingInstruction=_text(feature.get('FillingInstruction')),
                 Values=[dict(Value=_text(value.get('Value')), Okei=_text(value.get('Okei')))
                         for value in _array(feature.get('Values'))])
            for feature in _array(obj.get('Characteristics'))]


def _requirements_complete(position: TenderPosition) -> bool:
    if not position['name'] or not position['unit'] or position['quantity'] is None:
        return False
    for feature in position['characteristics']:
        instruction = feature['FillingInstruction'] or ''
        can_choose = ' '.join(instruction.casefold().split()).rstrip('.') == PARTICIPANT_VALUE
        if not _visible(feature['Name']) or MASK.search(instruction):
            return False
        values = feature['Values']
        if not values and not can_choose:
            return False
        for value in values:
            if any(text and MASK.search(text) for text in value.values()):
                return False
            if not value['Value'] and not can_choose:
                return False
    return True


def _totals(objects: list[dict]) -> dict[str, Decimal] | None:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    with localcontext() as context:
        context.prec = 70
        for obj in objects:
            name, unit = _visible(obj.get('Name')), _visible(obj.get('Okei'))
            quantity = _number(obj.get('Quantity'))
            if not name or not unit or quantity is None:
                return None
            identity = json.dumps([name, unit, _features(obj)], ensure_ascii=False, sort_keys=True)
            totals[identity] += Decimal(quantity)
    return totals


def normalize_tender(item: dict, card: dict, *, allow_cancelled: bool = False) -> TenderData:
    key = item.get('Id')
    title = _visible(card.get('Title')) or _visible(item.get('OrderName'))
    if not _valid_id(key) or card.get('Id') != key or not title:
        raise _invalid()
    if not allow_cancelled and _visible(card.get('CancelReason')):
        raise CoreError('KONTUR_TENDER_CLOSED', 'Выбранная закупка была отменена после поиска.')
    total = card.get('InitialSum')
    if total is not None and not isinstance(total, dict):
        raise _invalid()
    currency = _currency((total or {}).get('Currency')) or _currency(item.get('Currency'))
    lots = _array(card.get('Lots'))
    positions: list[TenderPosition] = []
    limitations: list[SourceLimitation] = []
    if not lots:
        limitations.append({'code': 'TENDER_OBJECTS_MISSING'})
    for lot_number, lot in enumerate(lots, 1):
        joint = _array(lot.get('JointObjects'))
        by_customer = [_array(customer.get('Objects')) for customer in _array(lot.get('Customers'))]
        breakdown = [(index, objects) for index, objects in enumerate(by_customer, 1) if objects]
        if breakdown and joint:
            customer_totals = _totals([obj for _, objects in breakdown for obj in objects])
            if customer_totals is None or customer_totals != _totals(joint):
                limitations.append({'code': 'LOT_OBJECTS_CONFLICT', 'lot': lot_number})
        use_joint = bool(joint) and (not breakdown or len(breakdown) < len(by_customer))
        if not joint and (not breakdown or len(breakdown) < len(by_customer)):
            limitations.append({'code': 'LOT_OBJECTS_MISSING', 'lot': lot_number})
        groups = [(None, joint)] if use_joint else breakdown
        for customer, objects in groups:
            for ordinal, obj in enumerate(objects, 1):
                position = TenderPosition(id=f'{lot_number}:{customer or 0}:{ordinal}', lot=lot_number,
                    customer=customer, name=_visible(obj.get('Name')), quantity=_number(obj.get('Quantity')),
                    unit=_visible(obj.get('Okei')), item_price=_number(obj.get('ItemPrice')),
                    position_price=_number(obj.get('PositionPrice')), currency=currency, characteristics=_features(obj))
                positions.append(position)
                if not _requirements_complete(position):
                    limitations.append({'code': 'POSITION_REQUIREMENTS_INCOMPLETE', 'lot': lot_number, 'position_id': position['id']})
    return TenderData(id=key, title=title, number=_visible(card.get('NotificationNumber')) or _visible(item.get('NotificationNumber')),
        link=_link(card.get('Link')) or _link(item.get('Link')), positions=positions, limitations=limitations)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class KonturRequestGate:
    """Shared request timing for clients of one origin; never holds HTTP open."""

    def __init__(self):
        self.last_request: float | None = None
        self.lock = Lock()

    def reserve(self, min_interval: float) -> None:
        with self.lock:
            if self.last_request is not None:
                sleep(max(0, min_interval - (monotonic() - self.last_request)))
            self.last_request = monotonic()


class KonturClient:
    def __init__(self, key: str, *, base_url: str = BASE_URL, opener=None, min_interval: float = 0.25,
                 allow_configured_api: bool = False, key_provider: Callable[[], str] | None = None,
                 provider_limits: Callable[[], list[dict]] | None = None,
                 request_gate: KonturRequestGate | None = None):
        base_url = base_url.rstrip('/')
        parsed = urlsplit(base_url)
        self.simulated = base_url != BASE_URL
        if (parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path
                or self.simulated and (parsed.scheme != 'http' or parsed.hostname not in
                    {'127.0.0.1', 'localhost', '::1', 'kontur-simulator'} or key != SIMULATOR_KEY)
                or not self.simulated and key == SIMULATOR_KEY
                or self.simulated and key_provider is not None or not self.simulated and provider_limits is not None):
            raise ValueError('Kontur simulator requires a local HTTP address and its dedicated test key')
        self.base_url = base_url
        self._key, self.key_provider = key, key_provider
        self.provider_limits = provider_limits
        self.allow_configured_api = allow_configured_api is True and not self.simulated
        handlers = [_NoRedirect(), ProxyHandler({})] if self.simulated else [_NoRedirect()]
        self.opener = opener if opener is not None else build_opener(*handlers)
        self.min_interval = min_interval
        self._request_gate = request_gate if request_gate is not None else KonturRequestGate()

    def _reserve_request(self) -> None:
        self._request_gate.reserve(self.min_interval)

    @property
    def key(self) -> str:
        return self.key_provider() if self.key_provider is not None else self._key

    def _request(self, path: str, body: dict | None = None, *, array: bool = False):
        key = self.key
        if not key:
            raise CoreError('KONTUR_UNCONFIGURED', 'Ключ Контур.Закупок не настроен на сервере.')
        if (not key.isascii() or any(not 33 <= ord(char) <= 126 for char in key)
                or not self.simulated and key == SIMULATOR_KEY):
            raise CoreError('KONTUR_AUTH_ERROR', 'Проверьте формат серверного ключа Контур.Закупок.')
        self._reserve_request()
        request = Request(self.base_url + path, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
            headers={'X-Kontur-Apikey': key, 'Accept': 'application/json', 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=25) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except HTTPError as error:
            status = error.code
            error.close()
            code, message = {
                400: ('KONTUR_QUERY_REJECTED', 'Контур отклонил параметры запроса.'),
                401: ('KONTUR_AUTH_ERROR', 'Контур отклонил ключ доступа.'),
                403: ('KONTUR_ACCESS_DENIED', 'Проверьте права ключа и тариф Контур.Закупок.'),
                404: ('KONTUR_NOT_FOUND', 'Контур не нашёл запрошенный ресурс.'),
                429: ('KONTUR_RATE_LIMIT', 'Контур ограничил запросы. Задание остановлено.'),
            }.get(status, ('KONTUR_UNAVAILABLE', 'Контур не смог выполнить запрос.'))
            raise CoreError(code, message) from None
        except (OSError, HTTPException):
            raise CoreError('KONTUR_UNAVAILABLE', 'Не удалось получить ответ Контур.Закупок.') from None
        if len(raw) > MAX_RESPONSE:
            raise CoreError('KONTUR_DATA_LIMIT', 'Ответ Контура превышает допустимый размер.')
        try:
            value = json.loads(raw, parse_float=Decimal, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, RecursionError):
            raise _invalid() from None
        if not isinstance(value, list if array else dict):
            raise _invalid()
        return json.loads(json.dumps(value, default=str))

    def reset_simulator_quota(self) -> list[dict]:
        from bervel.modules.ai_tender_radar.selection import require_simulator
        require_simulator(self)
        if self.provider_limits is not None:
            raise CoreError('CONFLICT', 'Квоты, полученные от действующего API, сбросить нельзя.')
        rows = self._request('/internal/quota/reset', {}).get('limits')
        if not isinstance(rows, list):
            raise _invalid()
        verified_limit_groups(rows)
        return rows

    def read_limits(self) -> list[dict]:
        from bervel.modules.ai_tender_radar.selection import require_simulator
        if not self.allow_configured_api:
            require_simulator(self)
        if self.provider_limits is None:
            result = self._request('/external/v1/limitGroups', array=True)
        else:
            provider = verified_limit_groups(self.provider_limits())
            if not {'search', 'purchase'} <= provider.keys():
                raise CoreError('KONTUR_ACCESS_DENIED', 'Контур не сообщил квоты поиска и подробных карточек для этого ключа.')
            fields = ('LimitGroup', 'AvailableRequests', 'TotalLimit', 'IsPeriodic', 'SubscriptionStart', 'SubscriptionEnd')
            quotas = [{key: provider[group][key] for key in fields if key in provider[group]} for group in ('search', 'purchase')]
            response = self._request('/internal/quota', {'limits': quotas})
            result = response.get('limits')
            if not isinstance(result, list):
                raise _invalid()
            local = verified_limit_groups(result)
            if set(local) != {'search', 'purchase'} or any(
                    local[group]['AvailableRequests'] > provider[group]['AvailableRequests']
                    or local[group]['TotalLimit'] != provider[group]['TotalLimit'] for group in local):
                raise _invalid()
        if any(not isinstance(group, dict) or type(group.get('AvailableRequests')) is not int
               or group['AvailableRequests'] < 0 for group in result):
            raise _invalid()
        return result

    def read_documents(self, card: dict) -> list[dict]:
        from bervel.modules.ai_tender_radar.selection import require_simulator
        require_simulator(self)
        documents = []
        base = urlsplit(self.base_url)
        for entry in (card.get('Docs') or [])[:5]:
            link = entry.get('Url')
            if not isinstance(link, str):
                continue
            parsed = urlsplit(link)
            if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc) or parsed.username or parsed.query or parsed.fragment:
                raise CoreError('KONTUR_INVALID_RESPONSE', 'Документ симуляции ссылается за пределы разрешённого источника.')
            if not parsed.path.startswith('/documents/'):
                raise CoreError('KONTUR_INVALID_RESPONSE', 'Неизвестный путь документа симуляции.')
            self._reserve_request()
            request = Request(link, headers={'X-Kontur-Apikey': self.key, 'Accept': 'text/plain'})
            try:
                with self.opener.open(request, timeout=25) as response:
                    content = response.read(256 * 1024 + 1)
                if len(content) > 256 * 1024:
                    raise CoreError('KONTUR_DATA_LIMIT', 'Документ превышает бюджет чтения.')
                text = content.decode('utf-8')
            except (OSError, HTTPException, UnicodeError):
                raise CoreError('KONTUR_UNAVAILABLE', 'Не удалось прочитать локальный документ симуляции.') from None
            documents.append({'filename': entry.get('FileName'), 'url': link, 'text': text})
        return documents

    def search_page(self, phrase: str | list[str], page: int, start: str, end: str, *, purchase_statuses: list[int] | None = None) -> dict:
        from bervel.modules.ai_tender_radar.selection import require_simulator
        if not self.allow_configured_api:
            require_simulator(self)
        keywords = [phrase] if isinstance(phrase, str) else phrase
        if (not 0 <= page < 200 or not isinstance(keywords, list) or not keywords
                or any(not isinstance(word, str) or not word.strip() or len(word) > 500 for word in keywords)):
            raise CoreError('VALIDATION_ERROR', 'Некорректная поисковая ветка или страница.')
        if purchase_statuses is not None and (not purchase_statuses or any(
                type(status) is not int or status not in PURCHASE_STATUS.values() for status in purchase_statuses)):
            raise CoreError('VALIDATION_ERROR', 'Выберите допустимые этапы закупок.')
        return self._request('/external/v1/search', {
            'Text': keywords, 'PurchaseStatuses': [1] if purchase_statuses is None else purchase_statuses, 'PageNumber': page,
            'DateTimeFrom': start, 'DateTimeTo': end,
            'Attachments': True, 'MaxPriceNone': True, 'AllowForeignCurrency': True,
        })

    def _search(self, phrase: str) -> Iterator[list[dict]]:
        end = datetime.now(MOSCOW).replace(microsecond=0)
        start = end - SEARCH_WINDOW
        for page in range(SEARCH_PAGES):
            result = self._request('/external/v1/search', {
                'Text': [phrase], 'PurchaseStatuses': [1], 'PageNumber': page,
                'DateTimeFrom': start.isoformat(), 'DateTimeTo': end.isoformat(),
                'Attachments': True, 'MaxPriceNone': True, 'AllowForeignCurrency': True,
            })
            total = result.get('TotalCount')
            items = _array(result.get('Items'))
            if type(total) is not int or total < 0 or result.get('PageNumber') != page or len(items) != min(PAGE_SIZE, max(0, total - page * PAGE_SIZE)):
                raise _invalid()
            if any(not _valid_id(item.get('Id')) for item in items):
                raise _invalid()
            yield [item for item in items if not any(item.get(flag) is True for flag in ('IsCancelled', 'IsAbandoned', 'IsPlanning'))]
            if (page + 1) * PAGE_SIZE >= total:
                return

    def fetch_next(self, phrase: str, processed_ids: Callable[[list[str]], set[str]]) -> TenderData | None:
        for items in self._search(phrase):
            processed = processed_ids([item['Id'] for item in items])
            for item in items:
                if item['Id'] not in processed:
                    return self.fetch_tender(item['Id'], search_item=item)
        return None

    def fetch_tender(self, key: str, *, search_item: dict | None = None, allow_cancelled: bool = False) -> TenderData:
        if not _valid_id(key):
            raise _invalid()
        card = self._request('/external/v1/purchases/' + quote(key, safe=''))
        tender = normalize_tender(search_item if search_item is not None else {'Id': key}, card,
                                  allow_cancelled=allow_cancelled)
        from bervel.modules.ai_tender_radar.completeness import extract_terms
        tender['terms'] = extract_terms(card)
        tender['source_card'] = json.loads(json.dumps(card, default=str))
        if self.simulated and card.get('Docs'):
            from bervel.modules.ai_tender_radar.completeness import enrich_from_documents
            tender = enrich_from_documents(tender, self.read_documents(card))
        if self.simulated:
            tender['simulated'] = True
        return tender
