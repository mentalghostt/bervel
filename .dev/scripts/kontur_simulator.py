"""Local Kontur.Procurement API simulator for the AI Tender Radar."""

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from functools import lru_cache
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from random import Random
import re
from tempfile import NamedTemporaryFile
from threading import Lock
from time import monotonic
from urllib.parse import unquote, urlsplit


TEST_KEY = 'bervel-kontur-simulator'
API_PREFIX = '/external/v1/'
QUOTA = 200
MOSCOW = timezone(timedelta(hours=3))
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
REALISTIC_REGIONS = {
    16: 'Татарстан', 23: 'Краснодарский край', 50: 'Подмосковье', 59: 'Пермский край',
    66: 'Свердловская область', 77: 'Москва', 78: 'Санкт-Петербург',
}
REALISTIC_ASSORTMENTS = {
    'Техническое обслуживание систем вентиляции': ('усл. ед', (
        'Очистка воздуховодов', 'Диагностика приводов заслонок', 'Балансировка вентиляторов', 'Проверка автоматики',
        'Замена воздушных фильтров', 'Измерение расхода воздуха', 'Испытание дымоудаления', 'Проверка ременных передач')),
    'Поставка молочных продуктов': ('упак', (
        'Молоко питьевое 2,5%', 'Молоко безлактозное 3,2%', 'Кефир 2,5%', 'Йогурт натуральный',
        'Творог 5%', 'Ряженка 3,2%', 'Сметана 15%', 'Сливки питьевые 10%')),
    'Поставка лекарственных препаратов': ('упак', (
        'Парацетамол 500 мг', 'Ибупрофен 200 мг', 'Натрия хлорид 0,9%', 'Хлоргексидин 0,05%',
        'Лоратадин 10 мг', 'Омепразол 20 мг', 'Дротаверин 40 мг', 'Декспантенол 5%')),
    'Сопровождение информационной системы': ('усл. ед', (
        'Сопровождение базы данных', 'Настройка резервного копирования', 'Миграция справочников', 'Проверка обновлений',
        'Настройка отчётности', 'Аудит ролей доступа', 'Тестирование восстановления', 'Обучение пользователей')),
    'Поставка автомобильного топлива': ('л', (
        'Бензин АИ-92', 'Бензин АИ-95', 'Бензин АИ-98', 'Дизельное топливо летнее',
        'Дизельное топливо зимнее', 'Дизельное топливо арктическое', 'Дизельное топливо межсезонное', 'Пропан-бутан автомобильный')),
    'Вывоз бытовых отходов': ('м3', (
        'Вывоз смешанных бытовых отходов', 'Вывоз макулатуры', 'Вывоз отходов упаковки', 'Вывоз крупногабаритных отходов',
        'Вывоз пищевых отходов', 'Вывоз стеклотары', 'Вывоз растительных отходов', 'Вывоз полимерных отходов')),
    'Организация школьного питания': ('порц', (
        'Горячий завтрак для начальных классов', 'Горячий обед для начальных классов', 'Горячий обед для старших классов',
        'Безлактозное меню', 'Безглютеновое меню', 'Полдник для группы продлённого дня', 'Завтрак в школьном лагере',
        'Обед в школьном лагере')),
    'Ремонт автомобильной дороги': ('м2', (
        'Фрезерование старого покрытия', 'Укладка верхнего слоя асфальта', 'Укладка выравнивающего слоя',
        'Ремонт обочин', 'Восстановление остановочных площадок', 'Ремонт тротуарного покрытия',
        'Укрепление откосов', 'Герметизация трещин покрытия')),
    'Печать информационных буклетов': ('шт', (
        'Буклет А4 с двумя сгибами', 'Листовка А5 двусторонняя', 'Брошюра А5 на скрепке', 'Плакат А3',
        'Карманный справочник А6', 'Каталог А4 в мягкой обложке', 'Открытка А6', 'Карта-схема А3 со сгибом')),
    'Поставка офисной мебели': ('шт', (
        'Стол письменный 1400 мм', 'Стол переговорный 2400 мм', 'Кресло офисное эргономичное', 'Тумба подкатная',
        'Шкаф архивный металлический', 'Стеллаж для документов', 'Стул для посетителей', 'Шкаф гардеробный двухсекционный')),
    'Поставка природного газа': ('м3', (
        'Газ для основной котельной', 'Газ для резервной котельной', 'Газ для сушильного цеха', 'Газ для термического участка',
        'Газ для испытательной лаборатории', 'Газ для административного здания', 'Газ для ремонтного корпуса',
        'Газ для складского комплекса')),
    'Поставка лабораторных приборов': ('шт', (
        'Спектрометр оптический', 'Весы аналитические', 'Дозатор лабораторный', 'pH-метр настольный',
        'Термостат суховоздушный', 'Центрифуга лабораторная', 'Микроскоп бинокулярный', 'Пробоотборник переносной')),
    'Перевозка пассажиров': ('рейс', (
        'Утренний рейс до промышленной зоны', 'Вечерний рейс от промышленной зоны', 'Трансфер от вокзала',
        'Трансфер до аэропорта', 'Рейс до учебного корпуса', 'Рейс между складскими площадками', 'Рейс до районного центра',
        'Дежурный маршрут выходного дня')),
    'Оказание медицинских услуг': ('усл. ед', (
        'Предварительный медосмотр', 'Периодический медосмотр', 'Флюорография', 'Электрокардиография',
        'Лабораторное исследование крови', 'Осмотр офтальмолога', 'Осмотр невролога', 'Профилактическая вакцинация')),
    'Уборка административных помещений': ('м2', (
        'Уборка входных зон', 'Уборка лестничных клеток', 'Уборка кабинетов', 'Мойка окон',
        'Уборка конференц-залов', 'Уборка складских помещений', 'Уборка санитарных помещений', 'Механизированная уборка полов')),
    'Юридическое сопровождение': ('усл. ед', (
        'Правовая экспертиза договора', 'Подготовка претензии', 'Сопровождение судебного заседания', 'Проверка закупочной документации',
        'Анализ корпоративных документов', 'Подготовка правового заключения', 'Сопровождение регистрации прав',
        'Проверка локальных нормативных актов')),
    'Охрана объектов': ('час', (
        'Охрана центрального проходного пункта', 'Охрана складского терминала', 'Патрулирование периметра', 'Дежурство в архиве',
        'Контроль доступа в административный корпус', 'Дежурство на грузовой проходной', 'Охрана ремонтной площадки',
        'Дежурство у диспетчерского центра')),
    'Поставка компьютерного оборудования': ('шт', (
        'Компьютер персональный', 'Ноутбук офисный', 'Монитор 27 дюймов', 'Сканер документный',
        'Коммутатор на 24 порта', 'Источник бесперебойного питания', 'Принтер лазерный', 'Док-станция USB-C')),
    'Поставка канцелярских товаров': ('упак', (
        'Бумага офисная А4', 'Бумага офисная А3', 'Папки-регистраторы', 'Файлы-вкладыши',
        'Ручки шариковые синие', 'Маркеры для досок', 'Конверты С4', 'Этикетки самоклеящиеся')),
}
REALISTIC_ANCILLARY = (
    ('Лента монтажная перфорированная', 'м'), ('Уплотнитель силиконовый', 'м'),
    ('Герметик резьбовой средней прочности', 'шт'), ('Паста противозадирная медная', 'шт'),
    ('Уголок монтажный оцинкованный', 'шт'), ('Шина монтажная перфорированная', 'м'),
    ('Набор сверл по металлу', 'комплект'), ('Маркер промышленный несмываемый', 'шт'),
    ('Профиль направляющий алюминиевый', 'м'), ('Труба гофрированная для кабеля', 'м'),
    ('Краска разметочная для металла', 'л'), ('Лента сигнальная самоклеящаяся', 'м'),
    ('Прокладка паронитовая для фланца', 'шт'), ('Панель ограждения оборудования', 'шт'),
    ('Хомут для защиты кабельного канала', 'шт'), ('Щётка для зачистки сварных швов', 'шт'),
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
    purchase_id: str | None = None
    title_text: str | None = None
    snippets_mode: str = 'present'
    content_kind: str | None = None
    requirements_variant: str = 'complete'
    economics_variant: str = 'complete'
    schedule_variant: str = 'complete'
    fixed_quantity: int | None = None
    supply_start: datetime | None = None
    coverage_positions: tuple[dict, ...] | None = None
    separate_lots: bool = False
    realistic_lot_count: int = 0

    @property
    def key(self) -> str:
        return self.purchase_id or f'SIM-{self.ordinal:05}'

    @property
    def title(self) -> str:
        return self.title_text or f'[Симуляция] {self.name} {self.standard}, закупка {self.ordinal}'

    @property
    def searchable(self) -> str:
        return self.title if self.content_kind else f'{self.name} {self.standard} закупка'

    @property
    def quantity(self) -> int:
        return self.fixed_quantity or 120 + (self.ordinal - 1) % 12 * 20

    def brief(self) -> dict:
        result = {'Id': self.key, 'NotificationNumber': self.key, 'OrderName': self.title,
                'TypeOfTrading': self.law,
                'CreateDate': self.published.isoformat(), 'ApplicationDeadline': self.deadline.isoformat(),
                'IsCancelled': self.status == 5, 'IsAbandoned': self.status == 6, 'IsPlanning': self.status == 4,
                'Currency': self.currency, 'MaxPrice': self.quantity * 100 if self.priced else None,
                'Link': f'https://kontur-simulator.invalid/{self.key}',
                'DeliveryPlaces': [f'Тестовый регион {self.region}'], 'Inns': ['7700000000'],
                'Snippets': [self.document or f'Поставка {self.name} по {self.standard}']}
        if self.snippets_mode == 'omitted':
            result.pop('Snippets')
        elif self.snippets_mode == 'null':
            result['Snippets'] = None
        elif self.snippets_mode == 'empty':
            result['Snippets'] = []
        elif self.snippets_mode == 'masked':
            result['Snippets'] = ['#####']
        elif self.snippets_mode == 'truncated':
            result['Snippets'] = [self.document[:24]]
        if self.requirements_variant == 'contact_mask':
            result['EpUri'] = '#####'
        if self.coverage_positions is not None:
            result['MaxPrice'] = self.total_price
        return result

    @property
    def total_price(self) -> float:
        return float(sum((Decimal(str(position['PositionPrice'])) for position in self.coverage_positions), Decimal(0)))

    def card(self) -> dict:
        if self.coverage_positions is not None:
            return self._coverage_card()
        position = {'Name': f'{self.name} {self.standard}'.strip(), 'Quantity': self.quantity, 'Okei': self.unit,
                    'ItemPrice': 100 if self.priced else None, 'PositionPrice': self.quantity * 100 if self.priced else None,
                    'Characteristics': [
                        {'Name': 'Номинальный диаметр', 'Values': [{'Value': self.thread.removeprefix('М'), 'Okei': 'мм'}]},
                        {'Name': 'Стандарт', 'Values': [{'Value': self.standard, 'Okei': None}]},
                    ] if self.standard else []}
        price = {'Price': self.quantity * 100, 'Currency': self.currency} if self.priced else None
        lot_price = {'Amount': self.quantity * 100, 'RubPrice': self.quantity * 100 if self.currency == 'RUB' else None,
                     'CurrencyCode': self.currency, 'Nds': None} if self.priced else None
        lots = [{'Title': self.title, 'Price': lot_price, 'JointObjects': [position], 'Customers': []}]
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
        result = {'Id': self.key, 'NotificationNumber': self.key, 'Title': self.title, 'InitialSum': price,
                'TypeOfTrading': self.law,
                'PublicationDateTimeUTC': self.published.isoformat(), 'ApplicationDeadline': self.deadline.isoformat(),
                'Link': f'https://kontur-simulator.invalid/{self.key}',
                'CancelReason': 'Тестовая отмена закупки' if self.status == 5 else None, 'Lots': lots}
        if self.content_kind is not None:
            self._selection_card(result, position)
        return result

    def _coverage_card(self) -> dict:
        # Round-trip creates fresh nested data: Handler may rewrite attachment URLs.
        positions = json.loads(json.dumps(self.coverage_positions))
        delivery = self.supply_start + timedelta(days=30)
        if self.realistic_lot_count:
            groups = [positions[index * len(positions) // self.realistic_lot_count:
                                (index + 1) * len(positions) // self.realistic_lot_count]
                      for index in range(self.realistic_lot_count)]
        else:
            groups = [[position] for position in positions] if self.separate_lots else [positions]
        lots = []
        for index, group in enumerate(groups):
            amount = float(sum((Decimal(position['PositionPrice']) for position in group), Decimal(0)))
            lot = {'Title': self.title, 'Price': {'Amount': amount, 'RubPrice': amount,
                'CurrencyCode': 'RUB', 'Nds': None}, 'JointObjects': group,
                'Customers': [{'StartContractDate': self.supply_start.isoformat(),
                    'EndContractDate': 'Поставка до ' + delivery.isoformat(), 'Objects': []}]}
            if self.realistic_lot_count:
                lot['Title'] = f'{self.title} — партия {index + 1}'
                variant = (self.variant + index) % 4
                if variant != 0:
                    lot['Customers'][0]['Objects'] = group
                    if variant != 1:
                        lot['JointObjects'] = []
                    if variant == 3 and len(group) > 1:
                        customer = lot['Customers'][0]
                        midpoint = len(group) // 2
                        lot['Customers'] = [dict(customer, Objects=group[:midpoint]), dict(customer, Objects=group[midpoint:])]
                if self.requirements_variant == 'empty_objects' and index == 0:
                    lot['JointObjects'] = []
                    for customer in lot['Customers']:
                        customer['Objects'] = []
                if self.economics_variant == 'missing_price' and index == 0:
                    group[0].update(ItemPrice=None, PositionPrice=None)
            lots.append(lot)
        return {'Id': self.key, 'NotificationNumber': self.key, 'Title': self.title,
            'InitialSum': {'Price': self.total_price, 'Currency': 'RUB'},
            'PublicationDateTimeUTC': self.published.isoformat(), 'ApplicationDeadline': self.deadline.isoformat(),
            'Link': f'https://kontur-simulator.invalid/{self.key}',
            'CancelReason': 'Тестовая отмена закупки' if self.status == 5 else None,
            'Lots': lots,
            'Docs': [{'FileName': self.key + '.txt', 'Url': '/documents/' + self.key + '.txt'}]}

    def _selection_card(self, card: dict, position: dict) -> None:
        lot = card['Lots'][0]
        # Procedural and delivery dates are intentionally distinct source facts.
        customer = {'StartContractDate': self.supply_start.isoformat(), 'EndContractDate': None, 'Objects': []}
        if self.schedule_variant == 'complete':
            customer['EndContractDate'] = 'Поставка до ' + (self.supply_start + timedelta(days=30)).isoformat()
        elif self.schedule_variant == 'contract_end_only':
            customer['EndContractDate'] = (self.supply_start + timedelta(days=30)).isoformat()
        lot['Customers'] = [customer]
        if self.content_kind == 'service':
            position.update(Name='Услуги технического обслуживания оборудования', Okei='усл. ед', Characteristics=[])
        elif self.content_kind == 'unrelated':
            position.update(Name='Бумага офисная А4', Okei='упак', Characteristics=[])
        elif self.content_kind == 'incompatible':
            position.update(Name='Болт М120 ГОСТ 7798-70', Characteristics=[
                {'Name': 'Номинальный диаметр', 'Values': [{'Value': '120', 'Okei': 'мм'}]}])
        elif self.content_kind == 'mixed':
            lot['JointObjects'].append({'Name': 'Монтаж оборудования', 'Quantity': 1, 'Okei': 'усл. ед',
                                       'ItemPrice': 500, 'PositionPrice': 500, 'Characteristics': []})
            total = self.quantity * 100 + 500
            card['InitialSum']['Price'] = total
            lot['Price'].update(Amount=total, RubPrice=total)
        if self.requirements_variant == 'missing_quantity':
            position['Quantity'] = None
        elif self.requirements_variant == 'masked_requirement':
            position['Characteristics'] = [{'Name': 'Обязательное покрытие', 'Values': [{'Value': '#####', 'Okei': None}]}]
        elif self.requirements_variant == 'empty_objects' or self.content_kind == 'document_product':
            lot['JointObjects'] = []
        if self.economics_variant == 'missing_price':
            position['ItemPrice'] = position['PositionPrice'] = None
        elif self.economics_variant == 'conflicting_prices':
            position['PositionPrice'] = self.quantity * 100 + 100
        if self.requirements_variant == 'contact_mask':
            card['ContactPerson'] = {'Name': '#####', 'Email': '#####', 'Phone': '#####'}
        # URL is injected by Handler from its bound listener, never a remote endpoint.
        card['Docs'] = [{'FileName': self.key + '.txt', 'Url': '/documents/' + self.key + '.txt'}]

    def document_text(self) -> str:
        if self.coverage_positions is not None:
            lines = ['Техническая спецификация закупки.', self.document]
            for position in self.coverage_positions:
                lines.append(f'Наименование: {position["Name"]}; количество: {position["Quantity"]}; единица: {position["Okei"]}.')
                for feature in position['Characteristics']:
                    lines.append(feature['Name'] + ': ' + feature['Values'][0]['Value'])
            lines.extend(['Начало производства: ' + self.supply_start.isoformat(), 'Срок доставки, дней: 2',
                'Поставка до: ' + (self.supply_start + timedelta(days=30)).isoformat(),
                'Логистика, RUB: 0', 'Прочие обязательные расходы, RUB: 0',
                'Налоговая база: цены и затраты сопоставимы',
                'Данные о расходах подтверждены: ' + (self.supply_start - timedelta(days=1)).isoformat()])
            return '\n'.join(lines) + '\n'
        if self.content_kind is None:
            return self.document
        spec_name = {'service': 'Услуги технического обслуживания оборудования', 'unrelated': 'Бумага офисная А4',
                     'incompatible': 'Болт М120 ГОСТ 7798-70'}.get(self.content_kind, f'{self.name} {self.standard}')
        unit = {'service': 'усл. ед', 'unrelated': 'упак'}.get(self.content_kind, self.unit)
        lines = ['Техническая спецификация закупки.', self.document,
                 f'Наименование: {spec_name}; количество: {self.quantity}; единица: {unit}.',
                 'Начало производства: ' + self.supply_start.isoformat(), 'Срок доставки, дней: 2',
                 'Логистика, RUB: 0', 'Прочие обязательные расходы, RUB: 0',
                 'Налоговая база: цены и затраты сопоставимы',
                 'Данные о расходах подтверждены: ' + (self.supply_start - timedelta(days=1)).isoformat()]
        if self.schedule_variant == 'complete':
            lines.append('Поставка до: ' + (self.supply_start + timedelta(days=30)).isoformat())
        elif self.schedule_variant == 'contract_end_only':
            lines.append('Срок окончания договора: ' + (self.supply_start + timedelta(days=30)).isoformat())
        if self.requirements_variant == 'masked_requirement':
            lines.append('Обязательное покрытие: #####')
        elif self.requirements_variant == 'missing_quantity':
            lines[2] = f'Наименование: {spec_name}; количество не указано.'
        if self.content_kind == 'mixed':
            lines.append('Дополнительно обязательно: Монтаж оборудования; количество: 1; единица: усл. ед.')
        return '\n'.join(lines) + '\n'


def selection_corpus(today: datetime, seed: int, snippets_mode: str) -> tuple[dict, dict]:
    """Private truth and wire records share opaque IDs, never encoded labels."""
    rng = Random(seed)
    snippets_rng = Random(seed ^ 0x534E4950)
    kinds = ['product', 'neutral_product', 'document_product', 'service', 'mixed', 'unrelated', 'incompatible'] * 60
    requirements = ['complete', 'complete', 'complete', 'contact_mask', 'missing_quantity', 'masked_requirement', 'empty_objects'] * 60
    economics = (['complete'] * 3 + ['missing_price', 'conflicting_prices']) * 84
    schedules = ['complete', 'complete', 'missing_date', 'contract_end_only'] * 105
    for values in (kinds, requirements, economics, schedules):
        rng.shuffle(values)
    purchases, truth = {}, {}
    for index, (kind, requirement, economy, schedule) in enumerate(zip(kinds, requirements, economics, schedules)):
        name, standard, thread = rng.choice(PRODUCTS)
        title = {'product': f'Закупка: поставка {name} {standard}', 'neutral_product': 'Закупка материалов для ремонтного участка',
                 'document_product': 'Закупка по технической спецификации', 'service': 'Закупка услуг технического обслуживания',
                 'mixed': f'Закупка: {name} {standard} и монтаж', 'unrelated': 'Закупка бумаги для офиса',
                 'incompatible': 'Закупка: Болт М120 ГОСТ 7798-70'}[kind]
        document = (f'Техническое обслуживание: {name} {standard} используется исполнителем как расходник.' if kind == 'service' else
                    'Поставка бумаги офисной А4.' if kind == 'unrelated' else
                    'Болт М120 ГОСТ 7798-70, диаметр 120 мм; М12 не является аналогом.' if kind == 'incompatible' else
                    f'Поставка {name} {standard}; требования приведены в спецификации.')
        mode = snippets_rng.choice(['present', 'omitted', 'null', 'empty', 'masked', 'truncated']) if snippets_mode == 'mixed' else snippets_mode
        purchase = Purchase(index + 1, today - timedelta(days=rng.randint(1, 60)), today + timedelta(days=14),
            name, standard, thread, document=document, purchase_id=f'SIM-{rng.getrandbits(128):032x}', title_text=title,
            snippets_mode=mode, content_kind=kind, requirements_variant=requirement, economics_variant=economy,
            schedule_variant=schedule, supply_start=today + timedelta(days=1), variant=0,
            fixed_quantity=rng.choice([100, 120, 200, 500]))
        purchases[purchase.key] = purchase
        req = 'complete' if requirement in {'complete', 'contact_mask'} and kind != 'document_product' else 'incomplete'
        truth[purchase.key] = {'kind': kind, 'relevant': kind in {'product', 'neutral_product', 'document_product'},
            'requirements': req, 'economics': 'complete' if economy == 'complete' and requirement not in {'missing_quantity', 'empty_objects'} and kind != 'document_product' else 'unknown',
            'schedule': 'complete' if schedule == 'complete' else 'unknown', 'harmless_contact_mask': requirement == 'contact_mask',
            'all_complete': req == 'complete' and economy == 'complete' and schedule == 'complete'}
    # Two briefs convey exactly the same useful information; detail decides completeness.
    for requirement in ('complete', 'masked_requirement'):
        purchase = Purchase(1, today - timedelta(days=2), today + timedelta(days=14), *PRODUCTS[0],
            purchase_id=f'SIM-{rng.getrandbits(128):032x}', title_text='Закупка: поставка Болт М12×50 ГОСТ 7798-70',
            document='Поставка Болт М12×50 ГОСТ 7798-70.', snippets_mode='present' if snippets_mode == 'mixed' else snippets_mode, content_kind='product',
            requirements_variant=requirement, supply_start=today + timedelta(days=1), fixed_quantity=120)
        purchases[purchase.key] = purchase
        truth[purchase.key] = {'kind': 'product', 'relevant': True, 'requirements': 'complete' if requirement == 'complete' else 'incomplete',
            'economics': 'complete', 'schedule': 'complete', 'all_complete': requirement == 'complete',
            'control_pair': True, 'harmless_contact_mask': False}
    items = list(purchases.items())
    rng.shuffle(items)
    return dict(items), truth


def _realistic_basket(positions):
    quantities = {}
    aliases = {'166': 'кг', 'килограмм': 'кг', '796': 'шт', 'штука': 'шт', 'шт.': 'шт'}
    for position in positions:
        unit = position['Okei'].casefold()
        requirements = tuple(sorted((search_text(item['Name']), search_text(value['Value']),
                                     search_text(value['Okei'] or ''))
                                    for item in position['Characteristics'] for value in item['Values']))
        key = search_text(position['Name']), aliases.get(unit, unit), requirements
        quantities[key] = quantities.get(key, Decimal(0)) + Decimal(position['Quantity'])
    return frozenset(quantities), frozenset(quantities.items())


def _realistic_line(line, product):
    quantity = Decimal(line['Quantity'])
    if product['Okei'].casefold() not in {'кг', '166', 'килограмм'}:
        quantity = quantity.to_integral_value()
    total = (Decimal(line['ItemPrice']) * quantity).quantize(Decimal('.01'))
    return dict(line, **product, Quantity=str(quantity), PositionPrice=str(total))


def _unique_realistic_positions(positions, visible_start, full_match, related, sector, unmatched, pool, rng,
                                used_assortments, used_baskets):
    for attempt in range(256):
        candidate = list(positions)
        if full_match and attempt:
            index = rng.randrange(len(candidate))
            if len(pool) >= 16:
                _, template = rng.choice(pool)
                candidate[index] = _realistic_line(candidate[index], template)
            else:
                line = candidate[index]
                quantity = Decimal(line['Quantity']) + attempt
                total = (Decimal(line['ItemPrice']) * quantity).quantize(Decimal('.01'))
                candidate[index] = dict(line, Quantity=str(quantity), PositionPrice=str(total))
        elif not full_match and related:
            # Keep the last service line. Spread the ancillary assortment over
            # visible lines even if the first lot intentionally has no detail.
            size = rng.randint(3, min(8, len(candidate) - visible_start - 1))
            slots = set(unmatched)
            remaining = [index for index in range(visible_start, len(candidate) - 1) if index not in slots]
            slots.update(rng.sample(remaining, max(0, size - sum(index >= visible_start for index in slots))))
            products = rng.sample(REALISTIC_ANCILLARY, size)
            for offset, index in enumerate(sorted(slots)):
                name, unit = products[offset % size]
                candidate[index] = _realistic_line(candidate[index], {'Name': name, 'Okei': unit, 'Characteristics': []})
        elif not full_match:
            unit, catalog = REALISTIC_ASSORTMENTS[sector]
            size = rng.randint(2, min(len(catalog), len(candidate) - visible_start))
            products = rng.sample(catalog, size)
            for index, line in enumerate(candidate):
                candidate[index] = _realistic_line(line, {
                    'Name': products[index % size], 'Okei': unit, 'Characteristics': []})
        assortment, basket = _realistic_basket(candidate[visible_start:])
        # A single-SKU test stock cannot offer 30 different assortments. Those
        # positive baskets are distinguished by the real quantities required.
        unique_assortment = assortment not in used_assortments or (full_match and len(pool) < 16)
        if unique_assortment and basket not in used_baskets:
            used_assortments.add(assortment)
            used_baskets.add(basket)
            return candidate
    raise ValueError('Unable to generate a distinct realistic procurement basket')


def _realistic_title(topic, region, lots, positions, delivery, related, rng, used_titles):
    programs = (
        ('плановый ремонт производственной линии', 'сборка оборудования', 'пополнение ремонтного запаса',
         'модернизация цеха', 'обслуживание насосных станций', 'переоснащение участка',
         'восстановление конвейеров', 'комплектование монтажных бригад', 'подготовка к остановочному ремонту')
        if related else
        ('годовая программа снабжения', 'заявка территориальных подразделений', 'обеспечение новых площадок',
         'плановая потребность учреждения', 'закупка для филиалов', 'централизованная заявка',
         'исполнение по графику заказчика', 'обеспечение сезонных работ', 'обновление материальной базы')
    )
    location = REALISTIC_REGIONS[region]
    shapes = (
        '{topic} — {program}, {location}, {lots} лот., исполнение до {delivery}',
        '{topic} для программы «{program}» · {location} · {positions} поз. · срок до {delivery}',
        '{location}: {topic}, {program} · {lots} лот. и {positions} поз. · до {delivery}',
    )
    for attempt in range(128):
        title = rng.choice(shapes).format(topic=topic, program=rng.choice(programs), location=location,
            lots=lots, positions=positions, delivery=delivery.strftime('%d.%m.%Y'))
        if (key := search_text(title)) not in used_titles:
            used_titles.add(key)
            return title
    raise ValueError('Unable to generate a distinct realistic procurement title')


def realistic_corpus(today: datetime, seed: int, *, stock_csv: bytes,
                     positive_count: int = 30, negative_count: int = 4970):
    from bervel.modules.ai_tender_radar.infrastructure.kontur import _features
    from bervel.modules.ai_tender_radar.infrastructure.stock import read_stock
    from bervel.modules.ai_tender_radar.matching import _catalog_item, _prepare

    if positive_count < 1 or negative_count < 0:
        raise ValueError('Expected positive and nonnegative corpus counts')
    rows = read_stock(stock_csv)['rows']
    catalog = [_catalog_item(row) for row in rows]
    rng = Random(seed)
    rng.shuffle(rows)
    pool = []
    for row in rows:
        position = {'Name': row['product'], 'Okei': row['unit'], 'Characteristics': []}
        for field, label, unit in [('standard', 'Стандарт', None), ('thread_diameter_mm', 'Диаметр резьбы', 'мм'),
                                   ('length_mm', 'Длина', 'мм')]:
            if value := row['attributes'].get(field):
                position['Characteristics'].append({'Name': label, 'Values': [{'Value': value, 'Okei': unit}]})
        probe = dict(id='probe', name=position['Name'], unit=position['Okei'], characteristics=_features(position))
        prepared, _ = _prepare(probe, catalog)
        if prepared and any(candidate['sku'] == row['sku'] for candidate in prepared['candidates']):
            pool.append((row, position))
        if len(pool) == 96:
            break
    if not pool:
        raise ValueError('Realistic corpus needs at least one verified stock item')

    related_titles = ('Поставка крепёжных изделий для промышленного оборудования',
                      'Закупка метизов для ремонтной программы',
                      'Поставка болтов, гаек и шайб для сборочных работ',
                      'Крепёжные изделия по ведомости потребности',
                      'Комплектование производственных участков метизами')
    targets = (500_000, 1_000_000, 3_000_000, 10_000_000)
    purchases, truth = {}, {}
    used_titles, used_assortments, used_baskets = set(), set(), set()
    labels = [True] * positive_count + [False] * negative_count
    rng.shuffle(labels)
    for index, full_match in enumerate(labels):
        related = full_match or rng.random() < .60
        title = rng.choice(related_titles)
        sector, unrelated, unrelated_unit, document = rng.choice(UNRELATED)
        position_count = rng.randint(100, 240) if index % 47 == 0 else rng.randint(10, 42)
        lot_count = rng.randint(15, min(42, position_count)) if index % 47 == 0 else rng.randint(2, min(12, position_count))
        target = Decimal(rng.choice(targets)) * Decimal(rng.randrange(90, 111)) / 100
        positions, unmatched = [], []
        for ordinal in range(position_count):
            row, template = rng.choice(pool)
            if related and (full_match or ordinal == 0 or rng.random() < .65):
                position = dict(template)
                unit_cost = Decimal(row['unit_cost'] or '0')
                unit_price = max(Decimal('9.00'), unit_cost * Decimal(rng.choice(('1.15', '1.3', '1.65', '2.15'))))
                weight = row['attributes'].get('weight_per_piece_kg', '').replace(',', '.')
                if weight and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', weight) and Decimal(weight) > 0 and rng.random() < .27:
                    position['Okei'] = rng.choice(('кг', '166', 'Килограмм'))
                    unit_price /= Decimal(weight)
            else:
                position = {'Name': unrelated, 'Okei': unrelated_unit, 'Characteristics': []}
                unit_price = Decimal(rng.randrange(80, 1800))
                if ordinal < position_count - 1:
                    unmatched.append(ordinal)
            if not full_match and related and ordinal == position_count - 1:
                position = {'Name': 'Услуги монтажа и технического обслуживания оборудования',
                            'Okei': 'усл. ед', 'Characteristics': []}
                unit_price = max(Decimal('1000'), target / position_count)
            price = unit_price.quantize(Decimal('.01'))
            line_target = target * Decimal(rng.randint(65, 135)) / (position_count * 100)
            quantity = max(Decimal(1), (line_target / price).to_integral_value())
            if position['Okei'].casefold() in {'кг', '166', 'килограмм'} and rng.random() < .55:
                quantity += Decimal(rng.randint(1, 9)) / 10
                price = price.quantize(Decimal('.1'))
            total = (price * quantity).quantize(Decimal('.01'))
            position.update(Quantity=str(quantity), ItemPrice=str(price), PositionPrice=str(total))
            positions.append(position)
        status = 1 if full_match or rng.random() < .88 else rng.choice((2, 3, 4, 5, 6))
        publication = today - timedelta(days=rng.randrange(1, 75))
        deadline = today + timedelta(days=rng.randrange(5, 46) if status == 1 else -rng.randrange(1, 28))
        snippets = rng.choice(('present', 'present', 'omitted', 'empty', 'truncated'))
        region, law = rng.choice((16, 23, 50, 59, 66, 77, 78)), rng.choice((1, 2))
        purchase_id = f'SIM-{rng.getrandbits(128):032x}'
        supply_start = deadline + timedelta(days=rng.randrange(5, 21))
        # Cosmetic and assortment retries never change the old identifier stream.
        variety = Random(f'{seed}:{purchase_id}:realistic-variety')
        empty_lot = not full_match and index % 19 == 0
        visible_start = position_count // lot_count if empty_lot else 0
        positions = _unique_realistic_positions(positions, visible_start, full_match, related, sector,
            unmatched, pool, variety, used_assortments, used_baskets)
        title = _realistic_title(title if related else sector, region, lot_count, position_count,
                                 supply_start + timedelta(days=30), related, variety, used_titles)
        purchase = Purchase(index + 1, publication, deadline, title, '', '', status=status,
                            region=region, law=law,
                            document='; '.join(position['Name'] for position in positions[:3]),
                            purchase_id=purchase_id, title_text=title, snippets_mode=snippets,
                            content_kind='product', supply_start=supply_start,
                            coverage_positions=tuple(positions), realistic_lot_count=lot_count, variant=index % 4,
                            requirements_variant='empty_objects' if empty_lot else 'complete',
                            economics_variant='missing_price' if not full_match and index % 23 == 0 else 'complete')
        purchases[purchase.key] = purchase
        truth[purchase.key] = {'full_catalog_match': full_match, 'required_positions': position_count, 'lots': lot_count}
    return purchases, truth


def _date(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('Dates must include a timezone')
    return result


def coverage_corpus(today: datetime, seed: int, snippets_mode: str, *, stock_csv: bytes,
                    positive_count: int = 150, negative_count: int = 1200,
                    split: str = 'development', discrete_quantities: bool = False) -> tuple[dict, dict]:
    """Generate ordinary wire records and separate, evidence-based evaluator labels.

    No learned selector is imported. The supplied stock is only read. Ground truth
    uses the existing requirement evidence and allocation calculator without an
    LLM: exact source facts establish SKU compatibility. Unknown units stay unknown.
    """
    from bervel.modules.ai_tender_radar.infrastructure.stock import read_stock
    from bervel.modules.ai_tender_radar.infrastructure.kontur import normalize_tender
    from bervel.modules.ai_tender_radar.matching import _catalog_item, _prepare, MatchingResult, PositionMatches
    from bervel.modules.ai_tender_radar.calculation import calculate_analysis

    if type(positive_count) is not int or positive_count < 1 or type(negative_count) is not int or negative_count < 0:
        raise ValueError('Coverage corpus counts must be positive/nonnegative integers')
    if split not in {'development', 'holdout'}:
        raise ValueError('Unknown coverage split')
    if type(discrete_quantities) is not bool:
        raise ValueError('discrete_quantities must be a boolean')
    if not isinstance(stock_csv, bytes) or not stock_csv:
        raise ValueError('Coverage profile requires stock CSV bytes')
    stock = read_stock(stock_csv)
    fingerprint = hashlib.sha256(stock_csv).hexdigest()
    salt = int.from_bytes(hashlib.sha256(f'{seed}/{split}'.encode()).digest()[:8], 'big')
    rng, snippets_rng = Random(salt), Random(salt ^ 0x534E4950)
    catalog = [_catalog_item(row) for row in stock['rows']]
    rows = {row['sku']: row for row in stock['rows']}
    eligible = [row for row in stock['rows'] if row['unit'].casefold().rstrip('.') in {'шт', 'штука', '796'}
                and row['free_quantity'] is not None and Decimal(row['free_quantity']) > 0]
    rng.shuffle(eligible)
    pool = []
    for row in eligible:
        # Exact names retain all technical qualifications in the supplied stock.
        source = {'Name': row['product'], 'Quantity': 1, 'Okei': row['unit'],
                  'ItemPrice': '1.00', 'PositionPrice': '1.00', 'Characteristics': []}
        for field, label, unit in [('standard', 'Стандарт', None),
                                   ('thread_diameter_mm', 'Диаметр резьбы', 'мм'), ('length_mm', 'Длина', 'мм')]:
            if value := row['attributes'].get(field):
                source['Characteristics'].append({'Name': label, 'Values': [{'Value': value, 'Okei': unit}]})
        probe = Purchase(1, today, today + timedelta(days=14), row['product'], '', '',
                         supply_start=today, content_kind='product', coverage_positions=(source,))
        tender = normalize_tender(probe.brief(), probe.card())
        prepared, uncertain = _prepare(tender['positions'][0], catalog)
        candidates = [] if prepared is None else prepared['candidates']
        if row['sku'] in {candidate['sku'] for candidate in candidates} and all(
                rows[candidate['sku']]['unit'] == row['unit'] for candidate in candidates):
            available = sum((Decimal(rows[candidate['sku']]['free_quantity'] or '0') for candidate in candidates), Decimal(0))
            # At least 50 pieces keep floor-derived 80/90% groups within two
            # percentage points without inventing stock or fractional pieces.
            if available > 0 and (not discrete_quantities or available >= 50):
                pool.append((row, source, prepared, uncertain, available))
        if len(pool) >= 96:
            break
    if not pool:
        raise ValueError('Coverage stock must include verifiable positive piece quantities')
    near_count = positive_count // 6
    targets = [('full', '100')] * (positive_count - 2 * near_count) + [('near_90', '90')] * near_count + [('near_80', '80')] * near_count
    negative_cases = ['low_2', 'low_6', 'wrong_standard', 'wrong_size', 'wrong_unit', 'mixed', 'soap', 'company_name']
    targets.extend((negative_cases[index % len(negative_cases)], None) for index in range(negative_count))
    rng.shuffle(targets)
    titles = ['Закупка крепёжных изделий по спецификации', 'Поставка материалов для ремонтного участка',
              'Закупка метизов для промышленного оборудования', 'Обеспечение производственного цеха',
              'Поставка товаров для технического обслуживания', 'Закупка материалов по ведомости потребности']
    purchases, truth = {}, {}
    preparation_cache = {}
    for index, (case, target) in enumerate(targets):
        row, source, prepared, uncertain, available = rng.choice(pool)
        desired = Decimal(target or {'low_2': '2', 'low_6': '6'}.get(case, '100'))
        rounding = ROUND_FLOOR if discrete_quantities and target is not None else ROUND_CEILING
        demand = (available * 100 / desired).to_integral_value(rounding=rounding) if desired < 100 else Decimal(rng.randint(5, 250))
        demand = min(demand, available) if desired == 100 else demand
        price = (Decimal(row['unit_cost'] or '10') * Decimal(rng.choice(['1.12', '1.28', '1.55', '2.10']))).quantize(Decimal('.01'))
        position = json.loads(json.dumps(source))
        position.update(Quantity=str(demand), ItemPrice=str(price), PositionPrice=str(price * demand))
        positions = [position]
        if case == 'wrong_standard':
            # Real adjacent standards, not a magic synthetic number. The heading
            # cites the equipment/part being repaired; the characteristic is the
            # binding standard specified by the purchaser.
            alternate = 'ГОСТ 7805-70' if row['attributes'].get('standard') != 'ГОСТ 7805-70' else 'ГОСТ 7798-70'
            features = [feature for feature in position['Characteristics'] if feature['Name'] != 'Стандарт']
            position['Characteristics'] = features + [{'Name': 'Стандарт', 'Values': [{'Value': alternate, 'Okei': None}]}]
        elif case == 'wrong_size':
            diameter = Decimal(row['attributes'].get('thread_diameter_mm') or '12')
            features = [feature for feature in position['Characteristics'] if feature['Name'] != 'Диаметр резьбы']
            position['Characteristics'] = features + [{'Name': 'Диаметр резьбы', 'Values': [{'Value': str(diameter + 2), 'Okei': 'мм'}]}]
        elif case == 'wrong_unit':
            position['Okei'] = rng.choice(['м', 'л', 'упак'])
            position['Quantity'] = '12.75' if position['Okei'] != 'упак' else '120'
            position['PositionPrice'] = str(price * Decimal(position['Quantity']))
        elif case in {'soap', 'company_name'}:
            position.update(Name='Мыло жидкое хозяйственное', Okei='л', Quantity='25.5', Characteristics=[])
            position['PositionPrice'] = str(price * Decimal(position['Quantity']))
        elif case == 'mixed':
            positions.append({'Name': 'Услуги монтажа и технического обслуживания оборудования', 'Quantity': '1',
                'Okei': 'усл. ед', 'ItemPrice': '15000.50', 'PositionPrice': '15000.50', 'Characteristics': []})
        if len(positions) == 1 and rng.random() < .35:
            total_quantity = Decimal(position['Quantity'])
            first_quantity = (total_quantity / 2).to_integral_value(rounding=ROUND_CEILING) if position['Okei'] in {'шт', 'упак', '796'} else total_quantity / 2
            second_quantity = total_quantity - first_quantity
            if second_quantity > 0:
                second = json.loads(json.dumps(position))
                position.update(Quantity=str(first_quantity), PositionPrice=str(price * first_quantity))
                second.update(Quantity=str(second_quantity), PositionPrice=str(price * second_quantity))
                positions.append(second)
        mode = snippets_rng.choice(['present', 'omitted', 'null', 'empty', 'masked', 'truncated']) if snippets_mode == 'mixed' else snippets_mode
        # Title, IDs, date, price margin, snippet availability and ordering are drawn
        # independently of labels. Full requirements are in the ordinary card/docs.
        title = titles[index % len(titles)]
        document = f'Материалы ремонтного участка: {row["product"]}. Поставляемые товары перечислены в технической спецификации.'
        if case == 'company_name':
            document = f'Заказчик ООО «Метиз Болт Гайка». Объект поставки приведён в спецификации. Справочник оборудования: {row["product"]}.'
        purchase = Purchase(index + 1, today - timedelta(days=rng.randint(1, 60)), today + timedelta(days=rng.randint(7, 21)),
            row['product'], '', '', unit=row['unit'], document=document,
            purchase_id=f'SIM-{rng.getrandbits(128):032x}', title_text=title, snippets_mode=mode,
            content_kind='product', supply_start=today + timedelta(days=rng.randint(1, 5)),
            coverage_positions=tuple(positions), separate_lots=rng.choice([True, False]))
        tender = normalize_tender(purchase.brief(), purchase.card())
        matches, unresolved = {}, False
        for pos_index, pos in enumerate(tender['positions']):
            if pos_index == 0 and case not in {'wrong_standard', 'wrong_size', 'soap', 'company_name'}:
                line, maybe = prepared, uncertain
            else:
                cache_key = json.dumps([pos['name'], pos['characteristics']], sort_keys=True, ensure_ascii=False)
                if cache_key not in preparation_cache:
                    preparation_cache[cache_key] = _prepare(pos, catalog)
                line, maybe = preparation_cache[cache_key]
            by_sku = {}
            for candidate in [] if line is None else line['candidates']:
                by_sku[candidate['sku']] = [{'requirement': line['requirements'][requirement]['name'],
                    'tender_value': line['requirements'][requirement]['value'],
                    'stock_field': next(iter(fields)), 'stock_value': next(iter(fields.values()))}
                    for requirement, fields in candidate['evidence'].items()]
            # This evaluator measures confirmed exact-compatibility coverage. It
            # does not invent extra stock from partially supported alternatives.
            matches[pos['id']] = PositionMatches(by_sku, False)
            unresolved |= maybe
        analysis = calculate_analysis(tender, stock, MatchingResult(matches))
        coverage = analysis['coverage_percent']
        positive = target is not None
        if positive:
            valid_positive = coverage is not None
            if valid_positive:
                actual, expected = Decimal(coverage), Decimal(target)
                valid_positive = expected <= actual < expected + 2 if discrete_quantities else abs(actual - expected) < Decimal('.01')
            if not valid_positive:
                raise ValueError('Stock cannot establish requested positive coverage')
        purchases[purchase.key] = purchase
        truth[purchase.key] = {'positive': positive, 'case': case, 'target_coverage_percent': target,
            'coverage_percent': coverage, 'coverage_reason': analysis['coverage_reason'],
            'exact_match_alternatives_unconfirmed': unresolved, 'stock_sha256': fingerprint,
            'source_skus': [candidate['sku'] for candidate in prepared['candidates']],
            'seed': seed, 'split': split, 'required_positions': len(positions)}
    items = list(purchases.items())
    rng.shuffle(items)
    return dict(items), truth


class Simulator(ThreadingHTTPServer):
    def __init__(self, port: int, *, host: str = '127.0.0.1', max_requests_per_second: int = 5,
                 profile: str = 'legacy', seed: int = 7319, today: str | datetime | None = None,
                 snippets_mode: str | None = None, stock_csv: bytes | str | Path | None = None,
                 positive_count: int = 150, negative_count: int = 1200, split: str = 'development',
                 request_quota: int | None = None, discrete_quantities: bool = False,
                 require_real_quota: bool = False, quota_state: Path | None = None,
                 unlimited_requests: bool = False):
        if unlimited_requests and (request_quota is not None or require_real_quota or quota_state is not None):
            raise ValueError('Unlimited requests cannot be combined with quota settings')
        if profile not in {'legacy', 'selection', 'coverage', 'realistic'}:
            raise ValueError('Unknown simulator profile')
        mode = snippets_mode or ('mixed' if profile in {'selection', 'coverage', 'realistic'} else 'present')
        if mode not in {'present', 'mixed', 'omitted', 'null', 'empty', 'masked', 'truncated'}:
            raise ValueError('Unknown snippets mode')
        today = _date(today) if isinstance(today, str) else today or datetime.now(timezone.utc)
        if today.tzinfo is None:
            raise ValueError('Dates must include a timezone')
        today = today.replace(hour=0, minute=0, second=0, microsecond=0)
        self.profile, self.seed, self.today, self.snippets_mode = profile, seed, today, mode
        self.ground_truth = {}
        self.purchases: dict[str, Purchase] = {}
        if profile == 'selection':
            self.purchases, self.ground_truth = selection_corpus(today, seed, mode)
        elif profile == 'coverage':
            if stock_csv is None:
                raise ValueError('Coverage profile requires stock_csv')
            stock_bytes = stock_csv if isinstance(stock_csv, bytes) else Path(stock_csv).read_bytes()
            self.purchases, self.ground_truth = coverage_corpus(today, seed, mode, stock_csv=stock_bytes,
                positive_count=positive_count, negative_count=negative_count, split=split,
                discrete_quantities=discrete_quantities)
        elif profile == 'realistic':
            if stock_csv is None:
                raise ValueError('Realistic profile requires stock_csv')
            stock_bytes = stock_csv if isinstance(stock_csv, bytes) else Path(stock_csv).read_bytes()
            self.purchases, self.ground_truth = realistic_corpus(today, seed, stock_csv=stock_bytes,
                positive_count=positive_count, negative_count=negative_count)
        for index in range(PURCHASE_COUNT if profile == 'legacy' else 0):
            cycle, family = divmod(index, 20)
            if family == 0:
                name, standard, thread = PRODUCTS[cycle % len(PRODUCTS)]
                unit, document = 'шт', 'Метизный-резерв'
            else:
                title, name, unit, document = UNRELATED[family - 1]
                name = title + ' (' + name + ')'
                standard, thread = '', ''
            stage = cycle % 12
            purchase = Purchase(index + 1, today - timedelta(days=1 + cycle % 730),
                today + timedelta(days=14) if stage < 7 else today - timedelta(days=14), name, standard, thread,
                unit=unit, document=document, status=1 if stage < 7 else stage - 5,
                region=77 if cycle % 2 == 0 else 78, law=1 if cycle % 2 == 0 else 2,
                category=family + 1, variant=cycle % 11, currency='EUR' if cycle % 19 == 9 else 'RUB',
                priced=cycle % 17 != 8, snippets_mode=mode if mode != 'mixed' else 'present')
            self.purchases[purchase.key] = purchase
        self.request_quota = request_quota if request_quota is not None else (2000 if profile == 'coverage' else QUOTA)
        if type(self.request_quota) is not int or self.request_quota < 1:
            raise ValueError('Request quota must be a positive integer')
        self.remaining = {} if unlimited_requests else {'search': self.request_quota, 'purchase': self.request_quota}
        self.quota_lock = Lock()
        self.require_real_quota, self.quota_state = require_real_quota, quota_state
        self.provider_limits: dict[str, dict] = {}
        self.spent: dict[str, dict] = {}
        if quota_state is not None:
            try:
                self.spent = json.loads(quota_state.read_text(encoding='utf-8'))
            except FileNotFoundError:
                pass
            if (not isinstance(self.spent, dict) or self.spent.keys() - self.remaining.keys()
                    or any(not isinstance(row, dict) or not isinstance(row.get('window'), list)
                           or type(row.get('used')) is not int or row['used'] < 0 for row in self.spent.values())):
                raise ValueError('Invalid simulator quota state')
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
            if group is not None and self.require_real_quota:
                provider = self.provider_limits.get(group)
                if provider is None or self._quota_window(provider) != self.spent[group]['window'] or not self._available(group):
                    return 429
                spent = {**self.spent, group: {**self.spent[group], 'used': self.spent[group]['used'] + 1}}
                try:
                    self._persist_quota(spent)
                except OSError:
                    return 503
                self.spent = spent
            elif group in self.remaining:
                if self.remaining[group] == 0:
                    return 429
                self.remaining[group] -= 1
            return 200

    @staticmethod
    def _quota_window(limit: dict) -> list[str | None]:
        day = datetime.now(MOSCOW).date().isoformat() if limit['IsPeriodic'] else None
        return [limit.get('SubscriptionStart'), limit.get('SubscriptionEnd'), day]

    def _available(self, group: str) -> int:
        provider = self.provider_limits.get(group)
        return max(0, provider['AvailableRequests'] - self.spent[group]['used']) if provider else 0

    def _persist_quota(self, spent: dict) -> None:
        if self.quota_state is None:
            return
        temporary = None
        try:
            self.quota_state.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.quota_state.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(spent, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.quota_state)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def synchronize_quota(self, limits: object) -> list[dict]:
        if not self.require_real_quota or not isinstance(limits, list):
            raise ValueError('Live quota synchronization is unavailable')
        provider = {}
        for row in limits:
            if not isinstance(row, dict):
                raise ValueError('Invalid limit group')
            group = row.get('LimitGroup')
            available, total = row.get('AvailableRequests'), row.get('TotalLimit')
            if (not isinstance(group, str) or group not in self.remaining or group in provider
                    or type(available) is not int or type(total) is not int or not 0 <= available <= total
                    or type(row.get('IsPeriodic')) is not bool or any(row.get(field) is not None and
                    not isinstance(row[field], str) for field in ('SubscriptionStart', 'SubscriptionEnd'))):
                raise ValueError('Invalid limit group')
            provider[group] = row
        if provider.keys() != self.remaining.keys():
            raise ValueError('Search and purchase quotas are required')
        with self.quota_lock:
            spent = {}
            for group, row in provider.items():
                window = self._quota_window(row)
                prior = self.spent.get(group, {})
                spent[group] = {'window': window, 'used': prior['used'] if prior.get('window') == window else 0}
            self._persist_quota(spent)
            self.spent, self.provider_limits = spent, provider
            return self._limit_groups()

    def _limit_groups(self) -> list[dict]:
        groups = []
        for group, remaining in self.remaining.items():
            provider = self.provider_limits.get(group, {})
            if self.require_real_quota:
                remaining = self._available(group)
            groups.append(dict(LimitGroup=group, AvailableRequests=remaining,
                TotalLimit=provider.get('TotalLimit', 0 if self.require_real_quota else self.request_quota),
                IsPeriodic=provider.get('IsPeriodic', True),
                Modifiers=['Snippets'] if group == 'search' and self.snippets_mode != 'omitted' else []))
        return groups

    def limit_groups(self) -> list[dict]:
        with self.quota_lock:
            return self._limit_groups()

    def reset_quota(self) -> list[dict]:
        with self.quota_lock:
            if self.require_real_quota:
                raise ValueError('Provider-mirrored quotas cannot be reset')
            self.remaining = {group: self.request_quota for group in self.remaining}
            return self._limit_groups()

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
            price = purchase.total_price if purchase.coverage_positions is not None else purchase.quantity * 100
            if (not purchase.priced and not query.get('MaxPriceNone', False)
                    or purchase.priced and not low <= price <= high
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
        status = self.server.admit(group)
        if status == 200:
            return True
        message = 'Simulator quota state unavailable' if status == 503 else 'Request rate or quota exhausted'
        self.reply(status, {'Message': message})
        return False

    def read_json_body(self) -> dict:
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= 64 * 1024:
            raise ValueError('Invalid request body length')
        self.connection.settimeout(5)
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError('Expected a JSON object')
        return body

    def do_POST(self) -> None:
        if not self.authorized():
            return
        path = urlsplit(self.path).path
        if path == '/internal/quota/reset':
            try:
                if self.read_json_body():
                    raise ValueError('Expected an empty object')
                limits = self.server.reset_quota()
            except (TypeError, ValueError, RecursionError, TimeoutError):
                self.reply(400, {'Message': 'Simulator quota cannot be reset'})
            else:
                self.reply(200, {'limits': limits})
            return
        if path == '/internal/quota' and self.server.require_real_quota:
            try:
                limits = self.server.synchronize_quota(self.read_json_body().get('limits'))
            except (TypeError, ValueError, RecursionError, TimeoutError):
                self.reply(400, {'Message': 'Invalid quota synchronization request'})
            except OSError:
                self.reply(503, {'Message': 'Simulator quota state unavailable'})
            else:
                self.reply(200, {'limits': limits})
            return
        if path != API_PREFIX + 'search':
            self.reply(404, {'Message': 'Resource not found'})
            return
        if not self.consume('search'):
            return
        try:
            result = self.server.search(self.read_json_body())
        except (KeyError, TypeError, ValueError, RecursionError, TimeoutError):
            self.reply(400, {'Message': 'Invalid search request'})
            return
        self.reply(200, result)

    def do_GET(self) -> None:
        if not self.authorized():
            return
        path = urlsplit(self.path).path
        purchase_path = API_PREFIX + 'purchases/'
        document_path = '/documents/'
        if path == API_PREFIX + 'limitGroups':
            if self.consume(None):
                self.reply(200, self.server.limit_groups())
        elif path.startswith(purchase_path):
            if self.consume('purchase'):
                purchase = self.server.purchases.get(unquote(path[len(purchase_path):]))
                if purchase is None:
                    self.reply(404, {'Message': 'Purchase not found'})
                else:
                    card = purchase.card()
                    host = self.headers.get('Host', '')
                    try:
                        requested = urlsplit('http://' + host)
                        valid_host = (requested.hostname in {'127.0.0.1', 'localhost', '::1', 'kontur-simulator'}
                            and requested.netloc == host and not requested.username and not requested.password
                            and not requested.path and not requested.query and not requested.fragment
                            and (requested.port is None or 1 <= requested.port <= 65535))
                    except ValueError:
                        valid_host = False
                    origin = 'http://' + (host if valid_host else f'127.0.0.1:{self.server.server_port}')
                    for attachment in card.get('Docs') or []:
                        attachment['Url'] = origin + attachment['Url']
                    self.reply(200, card)
        elif path.startswith(document_path) and path.endswith('.txt'):
            key = unquote(path[len(document_path):-4])
            purchase = self.server.purchases.get(key)
            if purchase is None or purchase.content_kind is None:
                self.reply(404, {'Message': 'Document not found'})
            elif self.consume(None):
                body = purchase.document_text().encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        else:
            self.reply(404, {'Message': 'Resource not found'})

    def log_message(self, format: str, *args: object) -> None:
        pass


def create_server(port: int = 8765, *, host: str = '127.0.0.1', max_requests_per_second: int = 5,
                  profile: str = 'legacy', seed: int = 7319, today: str | datetime | None = None,
                  snippets_mode: str | None = None, stock_csv: bytes | str | Path | None = None,
                  positive_count: int = 150, negative_count: int = 1200, split: str = 'development',
                  request_quota: int | None = None, discrete_quantities: bool = False,
                  require_real_quota: bool = False, quota_state: Path | None = None,
                  unlimited_requests: bool = False) -> Simulator:
    return Simulator(port, host=host, max_requests_per_second=max_requests_per_second, profile=profile,
                     seed=seed, today=today, snippets_mode=snippets_mode, stock_csv=stock_csv,
                     positive_count=positive_count, negative_count=negative_count, split=split,
                     request_quota=request_quota, discrete_quantities=discrete_quantities,
                     require_real_quota=require_real_quota, quota_state=quota_state, unlimited_requests=unlimited_requests)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
        epilog='Use X-Kontur-Apikey: bervel-kontur-simulator.')
    parser.add_argument('--port', type=int, default=8765, help='Loopback HTTP port (default: 8765)')
    parser.add_argument('--host', choices=['127.0.0.1', '0.0.0.0'], default='127.0.0.1', help='Use 0.0.0.0 inside Docker')
    parser.add_argument('--profile', choices=['legacy', 'selection', 'coverage', 'realistic'], default='legacy')
    parser.add_argument('--seed', type=int, default=7319)
    parser.add_argument('--today', help='Fixed timezone-aware ISO date-time')
    parser.add_argument('--snippets-mode', choices=['present', 'mixed', 'omitted', 'null', 'empty', 'masked', 'truncated'])
    parser.add_argument('--stock-csv', type=Path, help='Read-only warehouse CSV required by coverage profile')
    parser.add_argument('--positive-count', type=int, default=150)
    parser.add_argument('--negative-count', type=int, default=1200)
    parser.add_argument('--split', choices=['development', 'holdout'], default='development')
    parser.add_argument('--discrete-quantities', action='store_true', help='Use integer floor demand for genuinely >=80/90% positives on small stock')
    parser.add_argument('--request-quota', type=int, help='Separate search/detail budgets; coverage default 2000, others 200')
    parser.add_argument('--no-request-quota', action='store_true', help='Disable cumulative search and detail quotas')
    parser.add_argument('--require-real-quota', action='store_true', help='Require live quota synchronization for searches and details')
    parser.add_argument('--quota-state', type=Path, help='Private persistent counters for live-quota mode')
    parser.add_argument('--max-requests-per-second', type=int, default=5)
    parser.add_argument('--truth-output', type=Path, help='Private evaluator JSON output; never available through HTTP')
    args = parser.parse_args()
    with create_server(args.port, host=args.host, profile=args.profile, seed=args.seed, today=args.today,
                       snippets_mode=args.snippets_mode, stock_csv=args.stock_csv, positive_count=args.positive_count,
                       negative_count=args.negative_count, split=args.split, request_quota=args.request_quota,
                       max_requests_per_second=args.max_requests_per_second,
                       discrete_quantities=args.discrete_quantities, require_real_quota=args.require_real_quota,
                       quota_state=args.quota_state, unlimited_requests=args.no_request_quota) as server:
        if args.truth_output:
            args.truth_output.write_text(json.dumps(server.ground_truth, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Kontur simulator listening on http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
