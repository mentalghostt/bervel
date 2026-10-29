"""Deterministic free-stock rules. No model, transport, storage or manufacturing plan."""
from collections import defaultdict
from decimal import Decimal,InvalidOperation
import re
import unicodedata

from bervel.modules.tender_radar.models import RequestedPosition,StockData

UNITS={
    'шт':('шт','штук','штука','штуки','pcs','pc','piece','pieces','ед'),
    'комплект':('комплект','комплекта','комплектов','компл','set','sets'),
    'кг':('кг','kg','килограмм','килограммов'), 'г':('г','g','грамм','граммов'),
    'м':('м','m','метр','метров'), 'л':('л','l','литр','литров'),
    'т':('т','t','тонна','тонн','тонны'), 'упаковка':('упаковка','упаковок','упак','упаковки'),
}
QUANTITY_PATTERN=r'(?<![\w.,+\-−])(\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?)\s*([a-zа-яё.]+)'


def number(value: str) -> Decimal:
    if not isinstance(value,str):raise ValueError('Quantity must be text')
    value=value.strip().replace('\u00a0',' ').replace('\u202f',' ')
    if not re.fullmatch(r'(?:\d+|\d{1,3}(?: \d{3})+)(?:[.,]\d{1,6})?',value):raise ValueError('Invalid quantity')
    compact=value.replace(' ','').replace(',','.')
    if sum(c.isdigit() for c in compact)>18:raise ValueError('Quantity is too large')
    try:return Decimal(compact)
    except InvalidOperation:raise ValueError('Invalid quantity') from None


def decimal_text(value: Decimal) -> str:
    result=format(value,'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def percentage(value) -> Decimal:
    if isinstance(value,bool):raise ValueError('Invalid percentage')
    try:result=Decimal(str(value))
    except InvalidOperation:raise ValueError('Invalid percentage') from None
    if not result.is_finite() or not 0<=result<=100 or result.as_tuple().exponent < -2:
        raise ValueError('Percentage must be 0..100 with at most two decimals')
    return result


def unit(value: str | None) -> str | None:
    normalized=(value or '').strip().casefold().rstrip('.')
    return next((canonical for canonical,aliases in UNITS.items() if normalized in aliases),None)


def norm(value: str) -> str:
    text=unicodedata.normalize('NFKC',value).casefold().replace('ё','е').replace(',','.')
    return re.sub(r'(?<![a-zа-я])м(?=\s*\d)','m',text)


def specs_present(specs: dict[str,str],quote: str) -> bool:
    text=norm(quote)
    # HRC tension-control assemblies require their own catalogue identity.
    if re.search(r'\bhrc\b|14399[\s-]*10',text) and not re.search(r'14399[\s-]*10',norm(specs.get('standard',''))):return False
    for key,value in specs.items():
        if not value:continue
        tokens=re.findall(r'[a-zа-я]+|\d+(?:\.\d+)?',norm(value))
        if not tokens:return False
        literal=r'[\s\-–—_.]*'.join(re.escape(t) for t in tokens)
        pattern=r'(?<![\w.])'+literal+r'(?!\d|\.\d)'
        if key=='length_mm':
            # A demand of 50pcs must never prove a 50mm bolt length.
            explicit=r'\b(?:длина|длиной|length|l)\s*[:=]?\s*'+literal+r'(?!\d|\.\d)'
            dimension=r'\bm\d+(?:\.\d+)?\s*[×xх*]\s*'+literal+r'(?!\d|\.\d)'
            if not re.search(explicit+'|'+dimension,text):return False
        elif key=='thread' and norm(value).isdigit():
            explicit=r'\b(?:диаметр|отверстие|отверстием|diameter)\s*[:=]?\s*'+literal+r'\s*(?:мм|mm)(?!\w)'
            if not re.search(explicit,text):return False
        else:
            if not re.search(pattern,text):return False
            if key=='coating' and re.search(r'\b(?:без|нет|не|without|no)\b[^.;:\n]{0,40}'+literal,text):return False
    return True


def quantity_in_quote(quantity: str,measure: str,quote: str) -> bool:
    expected=number(quantity)
    for match in re.finditer(QUANTITY_PATTERN,quote.casefold()):
        try:actual=number(match[1])
        except ValueError:continue
        same_unit=unit(match[2])==unit(measure) if unit(measure) else match[2].rstrip('.')==measure.casefold().rstrip('.')
        if actual==expected and same_unit:return True
    return False


def stock_check(positions: list[RequestedPosition],complete: bool,stock: StockData | None,allowed: Decimal) -> dict:
    reasons=[];checks=[];required=defaultdict(Decimal)
    catalogue={item['sku']:item for item in stock['items']} if stock else {}
    if not stock:reasons.append('Не выбран источник свободных остатков.')
    if not complete:reasons.append('Полный состав требуемых позиций не подтверждён.')
    if not positions:reasons.append('Нет подтверждённых товарных позиций с количеством.')
    for pos in positions:
        item=catalogue.get(pos['sku'])
        if item is None or not pos['exact_match']:
            reasons.append('Точное соответствие складскому SKU не установлено.');continue
        if not specs_present(item['specs'],pos['quote']):
            reasons.append(f"Характеристики SKU {item['sku']} не подтверждены цитатой тендера.");continue
        try:count=number(pos['quantity'])
        except ValueError:
            reasons.append(f"Количество SKU {item['sku']} неизвестно.");continue
        measure=unit(pos['unit'])
        if count<=0 or measure is None or measure!=unit(item['unit']):
            reasons.append(f"Количество или единица SKU {item['sku']} требуют проверки.");continue
        if measure in {'шт','комплект','упаковка'} and count!=count.to_integral_value():
            reasons.append(f"Для SKU {item['sku']} указано дробное число целых изделий.");continue
        required[item['sku']]+=count
    for sku,count in required.items():
        item=catalogue[sku]
        try:available=number(item['free_quantity'])
        except ValueError:
            reasons.append(f'Свободный остаток SKU {sku} неизвестен.');continue
        shortage=max(Decimal(0),count-available)
        checks.append(dict(sku=sku,product=item['product'],unit=item['unit'],required=decimal_text(count),
            available=decimal_text(available),shortage=decimal_text(shortage),
            shortage_percent=decimal_text((shortage*100/count).quantize(Decimal('0.0001'))),
            within_tolerance=shortage*100<=count*allowed,source_row=item['row']))
    status='insufficient' if any(not c['within_tolerance'] for c in checks) else 'unknown' if reasons or not checks else 'pass'
    return dict(status=status,allowed_shortage_percent=decimal_text(allowed),checks=checks,reasons=list(dict.fromkeys(reasons)))
