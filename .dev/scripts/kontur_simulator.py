"""Local Kontur.Procurement API simulator for the AI Tender Radar."""

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from threading import Lock
from time import monotonic
from urllib.parse import unquote, urlsplit


TEST_KEY = 'bervel-kontur-simulator'
API_PREFIX = '/external/v1/'
QUOTA = 200
PAGE_SIZE = 50
PAGE_COUNT = 200
PURCHASE_COUNT = 120_000
PRODUCTS = (
    ('Болт М12×50', 'ГОСТ 7798-70', 'М12'),
    ('Болт М16×80', 'ГОСТ 7805-70', 'М16'),
    ('Гайка М12', 'ГОСТ 5915-70', 'М12'),
    ('Шайба М16', 'ГОСТ 11371-78', 'М16'),
)
UNRELATED = (
    ('Техническое обслуживание систем вентиляции', 'Обслуживание оборудования', 'усл. ед',
     'Замена болтов М12 ГОСТ 7798-70 входит в техническое обслуживание'),
    ('Поставка молочных продуктов', 'Молоко питьевое', 'л', ''),
    ('Поставка лекарственных препаратов', 'Парацетамол', 'упак', ''),
    ('Сопровождение информационной системы', 'Услуги сопровождения ПО', 'усл. ед', ''),
    ('Поставка автомобильного топлива', 'Бензин АИ-95', 'л', ''),
    ('Вывоз бытовых отходов', 'Услуги по вывозу отходов', 'м3', ''),
    ('Организация школьного питания', 'Услуги столовой', 'усл. ед', ''),
    ('Ремонт автомобильной дороги', 'Укладка асфальтового покрытия', 'м2', ''),
    ('Печать информационных буклетов', 'Буклет рекламный', 'шт', ''),
    ('Поставка офисной мебели', 'Стол письменный', 'шт', ''),
    ('Поставка природного газа', 'Газ природный', 'м3', ''),
    ('Поставка лабораторных приборов', 'Спектрометр', 'шт', ''),
    ('Перевозка пассажиров', 'Услуги автобусной перевозки', 'усл. ед', ''),
    ('Оказание медицинских услуг', 'Профилактический медицинский осмотр', 'усл. ед', ''),
    ('Уборка административных помещений', 'Услуги уборки', 'м2', ''),
    ('Юридическое сопровождение', 'Юридическая консультация', 'усл. ед', ''),
    ('Охрана объектов', 'Услуги охраны', 'усл. ед', ''),
    ('Поставка компьютерного оборудования', 'Компьютер персональный', 'шт', ''),
    ('Поставка канцелярских товаров', 'Бумага офисная', 'упак', ''),
)
WORD_FORMS = {form: stem for stem, forms in {
    'болт': ('болт', 'болты', 'болта', 'болтов', 'болтам', 'болтами'),
    'гайка': ('гайка', 'гайки', 'гаек', 'гайку', 'гайками'),
    'шайба': ('шайба', 'шайбы', 'шайб', 'шайбу', 'шайбами'),
    'обслуживание': ('обслуживание', 'обслуживания', 'обслуживанию'),
    'поставка': ('поставка', 'поставки', 'поставку', 'поставок'),
}.items() for form in forms}


def search_text(value: str) -> str:
    return ' '.join(re.findall(r'\w+', value.casefold().replace('ё', 'е')))


@lru_cache(maxsize=4096)
def matches(expression: str, text: str, strict: bool) -> bool:
    words = search_text(text).split()
    if not strict:
        words = [WORD_FORMS.get(word, word) for word in words]
    for phrase in expression.split(','):
        terms = re.findall(r'\w+\*?', phrase.casefold().replace('ё', 'е'))
        if terms and all(any(word.startswith(term[:-1]) for word in words) if term.endswith('*')
                         else (term if strict else WORD_FORMS.get(term, term)) in words for term in terms):
            return True
    return False


@dataclass(frozen=True)
class Purchase:
    ordinal: int
    published: datetime
    deadline: datetime
    name: str
    standard: str
    thread: str
    unit: str = 'шт'
    document: str = ''
    status: int = 1
    region: int = 77
    law: int = 1
    category: int = 1
    variant: int = 0
    currency: str = 'RUB'
    priced: bool = True

    @property
    def key(self) -> str:
        return f'SIM-{self.ordinal:05}'

    @property
    def title(self) -> str:
        return f'[Симуляция] {self.name} {self.standard}, закупка {self.ordinal}'

    @property
    def searchable(self) -> str:
        return f'{self.name} {self.standard} закупка'

    @property
    def quantity(self) -> int:
        return 120 + (self.ordinal - 1) % 12 * 20

    def brief(self) -> dict:
        return {'Id': self.key, 'NotificationNumber': self.key, 'OrderName': self.title,
                'CreateDate': self.published.isoformat(), 'ApplicationDeadline': self.deadline.isoformat(),
                'IsCancelled': self.status == 5, 'IsAbandoned': self.status == 6, 'IsPlanning': self.status == 4,
                'Currency': self.currency, 'MaxPrice': self.quantity * 100 if self.priced else None,
                'Link': f'https://kontur-simulator.invalid/{self.key}',
                'DeliveryPlaces': [f'Тестовый регион {self.region}'], 'Inns': ['7700000000'],
                'Snippets': [self.document or f'Поставка {self.name} по {self.standard}']}

    def card(self) -> dict:
        position = {'Name': f'{self.name} {self.standard}'.strip(), 'Quantity': self.quantity, 'Okei': self.unit,
                    'ItemPrice': 100 if self.priced else None, 'PositionPrice': self.quantity * 100 if self.priced else None,
                    'Characteristics': [
                        {'Name': 'Номинальный диаметр', 'Values': [{'Value': self.thread.removeprefix('М'), 'Okei': 'мм'}]},
                        {'Name': 'Стандарт', 'Values': [{'Value': self.standard, 'Okei': None}]},
                    ] if self.standard else []}
        price = {'Price': self.quantity * 100, 'Currency': self.currency} if self.priced else None
        lots = [{'Title': self.title, 'Price': price, 'JointObjects': [position], 'Customers': []}]
        if self.standard:
            if self.variant == 6:
                lots[0]['JointObjects'].append({'Name': 'Краска фасадная', 'Quantity': 20, 'Okei': 'л'})
            elif self.variant == 7:
                lots[0]['JointObjects'] = []
            elif self.variant == 8:
                position['Characteristics'][0]['Values'][0]['Value'] = '#####'
            elif self.variant == 9:
                lots[0]['Customers'] = [{'Objects': [position]}]
            elif self.variant == 10:
                lots.append({'Title': 'Дополнительный лот', 'JointObjects': [dict(position)], 'Customers': []})
        return {'Id': self.key, 'NotificationNumber': self.key, 'Title': self.title, 'InitialSum': price,
                'PublicationDateTimeUTC': self.published.isoformat(), 'ApplicationDeadline': self.deadline.isoformat(),
                'Link': f'https://kontur-simulator.invalid/{self.key}',
                'CancelReason': 'Тестовая отмена закупки' if self.status == 5 else None, 'Lots': lots}


def _date(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('Dates must include a timezone')
    return result


class Simulator(ThreadingHTTPServer):
    def __init__(self, port: int, *, host: str = '127.0.0.1', max_requests_per_second: int = 5):
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        self.purchases: dict[str, Purchase] = {}
        for index in range(PURCHASE_COUNT):
            cycle, profile = divmod(index, 20)
            if profile == 0:
                name, standard, thread = PRODUCTS[cycle % len(PRODUCTS)]
                unit, document = 'шт', 'Метизный-резерв'
            else:
                title, name, unit, document = UNRELATED[profile - 1]
                name = title + ' (' + name + ')'
                standard, thread = '', ''
            stage = cycle % 12
            purchase = Purchase(index + 1, today - timedelta(days=1 + cycle % 730),
                today + timedelta(days=14) if stage < 7 else today - timedelta(days=14), name, standard, thread,
                unit=unit, document=document, status=1 if stage < 7 else stage - 5,
                region=77 if cycle % 2 == 0 else 78, law=1 if cycle % 2 == 0 else 2,
                category=profile + 1, variant=cycle % 11, currency='EUR' if cycle % 19 == 9 else 'RUB',
                priced=cycle % 17 != 8)
            self.purchases[purchase.key] = purchase
        self.remaining = {'search': QUOTA, 'purchase': QUOTA}
        self.quota_lock = Lock()
        self.max_requests_per_second = max_requests_per_second
        self.recent_requests: deque[float] = deque()
        super().__init__((host, port), Handler)

    def admit(self, group: str | None, *, now: float | None = None) -> int:
        current = monotonic() if now is None else now
        with self.quota_lock:
            while self.recent_requests and self.recent_requests[0] <= current - 1:
                self.recent_requests.popleft()
            if self.max_requests_per_second and len(self.recent_requests) >= self.max_requests_per_second:
                return 429
            self.recent_requests.append(current)
            if group is not None:
                if self.remaining[group] == 0:
                    return 429
                self.remaining[group] -= 1
            return 200

    def limit_groups(self) -> list[dict]:
        with self.quota_lock:
            return [dict(LimitGroup=group, AvailableRequests=remaining, TotalLimit=QUOTA, IsPeriodic=True,
                         Modifiers=['Snippets'] if group == 'search' else [])
                    for group, remaining in self.remaining.items()]

    def search(self, query: dict) -> dict:
        supported = {'Text', 'Exclude', 'StrictSearch', 'DateTimeFrom', 'DateTimeTo', 'PageNumber',
                     'PurchaseStatuses', 'Attachments', 'MaxPriceNone', 'AllowForeignCurrency',
                     'MaxPriceFrom', 'MaxPriceTo', 'ApplicationDeadlineFrom', 'ApplicationDeadlineTo',
                     'RegionIds', 'Laws', 'CategoryIds', 'IncludeInns', 'ExcludeInns'}
        if query.keys() - supported:
            raise ValueError('Unsupported simulator search parameter')
        start = _date(query['DateTimeFrom'])
        end = _date(query['DateTimeTo'])
        page = query.get('PageNumber', 0)
        text = query.get('Text', [])
        statuses = query.get('PurchaseStatuses', [])
        attachments = query.get('Attachments', False)
        strict = query.get('StrictSearch', False)
        exclusions = query.get('Exclude', [])
        if (start > end or type(page) is not int or not 0 <= page < PAGE_COUNT
                or not isinstance(text, list) or any(not isinstance(phrase, str) for phrase in text)
                or not isinstance(exclusions, list) or any(not isinstance(phrase, str) for phrase in exclusions)
                or not isinstance(statuses, list) or any(type(status) is not int or not 1 <= status <= 6 for status in statuses)
                or any(type(query.get(key, False)) is not bool for key in
                       ('Attachments', 'StrictSearch', 'MaxPriceNone', 'AllowForeignCurrency'))):
            raise ValueError('Invalid search parameters')
        for key in ('RegionIds', 'Laws', 'CategoryIds', 'IncludeInns', 'ExcludeInns'):
            if not isinstance(query.get(key, []), list):
                raise ValueError('Expected a filter array')
        low, high = query.get('MaxPriceFrom', 0), query.get('MaxPriceTo', 10 ** 18)
        if type(low) is not int or type(high) is not int or not 0 <= low <= high:
            raise ValueError('Invalid price range')
        deadline_start = _date(query['ApplicationDeadlineFrom']) if query.get('ApplicationDeadlineFrom') else start - timedelta(days=3650)
        deadline_end = _date(query['ApplicationDeadlineTo']) if query.get('ApplicationDeadlineTo') else end + timedelta(days=3650)
        found = []
        for purchase in self.purchases.values():
            if (not start <= purchase.published <= end or statuses and purchase.status not in statuses
                    or not deadline_start <= purchase.deadline <= deadline_end):
                continue
            if (not purchase.priced and not query.get('MaxPriceNone', False)
                    or purchase.priced and not low <= purchase.quantity * 100 <= high
                    or purchase.currency != 'RUB' and not query.get('AllowForeignCurrency', False)):
                continue
            if any(query.get(key) and value not in query[key] for key, value in (
                    ('RegionIds', purchase.region), ('Laws', purchase.law), ('CategoryIds', purchase.category))):
                continue
            if (query.get('IncludeInns') and '7700000000' not in query['IncludeInns']
                    or '7700000000' in query.get('ExcludeInns', [])):
                continue
            searchable = purchase.searchable + (' ' + purchase.document if attachments else '')
            if text and not any(matches(phrase, searchable, strict) for phrase in text):
                continue
            if any(matches(phrase, searchable, strict) for phrase in exclusions):
                continue
            found.append(purchase)
        offset = page * PAGE_SIZE
        return {'PageNumber': page, 'TotalCount': len(found),
                'Items': [purchase.brief() for purchase in found[offset:offset + PAGE_SIZE]]}


class Handler(BaseHTTPRequestHandler):
    server: Simulator

    def reply(self, status: int, data: dict | list) -> None:
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        if self.headers.get('X-Kontur-Apikey') == TEST_KEY:
            return True
        self.reply(401, {'Message': 'Use the simulator test key'})
        return False

    def consume(self, group: str | None) -> bool:
        if self.server.admit(group) == 200:
            return True
        self.reply(429, {'Message': 'Request rate or daily quota exhausted'})
        return False

    def do_POST(self) -> None:
        if not self.authorized():
            return
        if urlsplit(self.path).path != API_PREFIX + 'search':
            self.reply(404, {'Message': 'Resource not found'})
            return
        if not self.consume('search'):
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 64 * 1024:
                raise ValueError('Invalid request body length')
            self.connection.settimeout(5)
            query = json.loads(self.rfile.read(length))
            if not isinstance(query, dict):
                raise ValueError('Expected a JSON object')
            result = self.server.search(query)
        except (KeyError, TypeError, ValueError, RecursionError, TimeoutError):
            self.reply(400, {'Message': 'Invalid search request'})
            return
        self.reply(200, result)

    def do_GET(self) -> None:
        if not self.authorized():
            return
        path = urlsplit(self.path).path
        purchase_path = API_PREFIX + 'purchases/'
        if path == API_PREFIX + 'limitGroups':
            if self.consume(None):
                self.reply(200, self.server.limit_groups())
        elif path.startswith(purchase_path):
            if self.consume('purchase'):
                purchase = self.server.purchases.get(unquote(path[len(purchase_path):]))
                if purchase is None:
                    self.reply(404, {'Message': 'Purchase not found'})
                else:
                    self.reply(200, purchase.card())
        else:
            self.reply(404, {'Message': 'Resource not found'})

    def log_message(self, format: str, *args: object) -> None:
        pass


def create_server(port: int = 8765, *, host: str = '127.0.0.1', max_requests_per_second: int = 5) -> Simulator:
    return Simulator(port, host=host, max_requests_per_second=max_requests_per_second)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
        epilog='Use X-Kontur-Apikey: bervel-kontur-simulator. Restart the process to reset both quotas.')
    parser.add_argument('--port', type=int, default=8765, help='Loopback HTTP port (default: 8765)')
    parser.add_argument('--host', choices=['127.0.0.1', '0.0.0.0'], default='127.0.0.1', help='Use 0.0.0.0 inside Docker')
    args = parser.parse_args()
    with create_server(args.port, host=args.host) as server:
        print(f'Kontur simulator listening on http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
