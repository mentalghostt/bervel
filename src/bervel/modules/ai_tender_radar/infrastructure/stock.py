from datetime import datetime
from decimal import Decimal
import re

from bervel.core.csv_memory import read_csv
from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.models import StockData, StockRow


REQUIRED = {'sku', 'product', 'free_quantity', 'unit'}
STOCK_FIELDS = REQUIRED | {'unit_cost', 'currency', 'as_of', 'data_kind'}


def _number(value: str) -> str | None:
    if not value:
        return None
    if not re.fullmatch(r'[0-9]{1,30}(?:[.,][0-9]{1,12})?', value):
        raise ValueError('Ожидается неотрицательное число.')
    return format(Decimal(value.replace(',', '.')), 'f')


def read_stock(content: bytes) -> StockData:
    try:
        header, rows, _, truncated = read_csv(content)
    except CoreError as error:
        raise CoreError('VALIDATION_ERROR', 'Не удалось прочитать складской CSV целиком.') from error
    fields = [field.strip().casefold() for field in header]
    if truncated or '' in fields or len(set(fields)) != len(fields) or not REQUIRED <= set(fields):
        raise CoreError('VALIDATION_ERROR', 'Складской CSV должен целиком помещаться в лимит и содержать уникальные колонки sku, product, free_quantity, unit.')

    parsed: list[StockRow] = []
    skus: set[str] = set()
    kinds: set[str] = set()
    for index, cells in enumerate(rows, 2):
        if not any(cell.strip() for cell in cells):
            continue
        try:
            if len(cells) != len(fields):
                raise ValueError('Число ячеек отличается от заголовка.')
            row = dict(zip(fields, (cell.strip() for cell in cells)))
            sku, product, unit = row['sku'], row['product'], row['unit']
            if not sku or len(sku) > 100 or sku in skus or not product or not unit:
                raise ValueError('Проверьте уникальный SKU, наименование и единицу.')
            quantity = _number(row['free_quantity'])
            if quantity is not None and unit.casefold().rstrip('.') in {'шт', 'штука', 'штуки', 'комплект', 'компл', 'упаковка', 'упак', '796'}:
                count = Decimal(quantity)
                if count != count.to_integral_value():
                    raise ValueError('Число изделий, комплектов или упаковок должно быть целым.')
            currency = row.get('currency', '').upper() or None
            if currency is not None and not re.fullmatch('[A-Z]{3}', currency):
                raise ValueError('Валюта должна быть трёхбуквенным кодом.')
            as_of = row.get('as_of') or None
            if as_of and datetime.fromisoformat(as_of).utcoffset() is None:
                raise ValueError('Для даты остатка нужен часовой пояс.')
            kind = row.get('data_kind') or 'reported'
            if kind not in {'mock', 'reported'}:
                raise ValueError('Допустимые типы данных — mock и reported.')
            parsed.append(StockRow(row=index, sku=sku, product=product, unit=unit, free_quantity=quantity,
                unit_cost=_number(row.get('unit_cost', '')), currency=currency, as_of=as_of,
                attributes={key: value for key, value in row.items() if key not in STOCK_FIELDS and value}))
            skus.add(sku)
            kinds.add(kind)
        except ValueError as error:
            raise CoreError('VALIDATION_ERROR', f'Ошибка в складском CSV, строка {index}. {error}') from None
    if len(kinds) != 1:
        raise CoreError('VALIDATION_ERROR', 'Нужен непустой складской CSV с единым типом данных.')
    return StockData(data_kind=kinds.pop(), rows=parsed)
