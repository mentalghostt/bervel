"""Source completeness is separate from factory feasibility and profitability."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re


def _iso(value):
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value)
        return value if date.utcoffset() is not None else None
    except ValueError:
        return None


def extract_terms(card: dict) -> dict:
    # Contract end and application dates are not, by themselves, delivery promises.
    evidence = []
    deadlines, starts = [], []
    lots = card.get('Lots') or []
    all_scheduled = bool(lots)
    for index, lot in enumerate(lots):
        customers = lot.get('Customers') or []
        all_scheduled &= bool(customers)
        for ordinal, customer in enumerate(customers):
            text = customer.get('EndContractDate')
            confirmed = False
            if isinstance(text, str):
                match = re.search(r'(?i)(?:поставка|доставка)\s+до\s+(\d{4}-\d\d-\d\dT[^\s;]+)', text)
                if match and _iso(match[1]):
                    deadlines.append(match[1])
                    confirmed = True
                    evidence.append({'path': f'Lots[{index}].Customers[{ordinal}].EndContractDate', 'quote': match[0]})
            start = _iso(customer.get('StartContractDate'))
            if start:
                starts.append(start)
            all_scheduled &= confirmed
    return {'delivery_deadline': min(deadlines, key=datetime.fromisoformat) if deadlines else None,
            'start_at': max(starts, key=datetime.fromisoformat) if starts else None,
            'delivery_days': None, 'logistics_cost': None, 'other_cost': None,
            'tax_comparable': None, 'evidence': evidence, 'source_schedule_complete': all_scheduled}


def check_completeness(tender: dict) -> dict:
    positions = tender.get('positions') or []
    limitations = tender.get('limitations') or []
    requirements = 'incomplete' if not positions or limitations else 'complete'
    documents = tender.get('documents') or []
    source_documents = tender.get('source_card', {}).get('Docs') or []
    document_reasons = []
    if len(documents) < len(source_documents):
        requirements = 'unknown' if requirements == 'complete' else requirements
        document_reasons.append('DOCUMENTS_NOT_FULLY_READ')
    for document in documents:
        text = document.get('text') or ''
        if re.search(r'(?i)(?:обязательн|требован|характеристик|покрытие|прочност|размер)[^\n]{0,120}(?:#{3,}|[░▒▓█]{2,})', text):
            requirements = 'incomplete'
            document_reasons.append('DOCUMENT_REQUIREMENTS_INCOMPLETE')
    prices = bool(positions)
    for position in positions:
        try:
            quantity = Decimal(position.get('quantity') or '0')
            item = position.get('item_price')
            total = position.get('position_price')
            if quantity <= 0 or not position.get('currency') or (item is None and total is None):
                prices = False
            elif item is not None and total is not None and abs(Decimal(item) * quantity - Decimal(total)) > Decimal('.01'):
                prices = False
        except (InvalidOperation, TypeError):
            prices = False
    terms = tender.get('terms') or {}
    schedule = 'complete' if _iso(terms.get('delivery_deadline')) and terms.get('source_schedule_complete') is not False else 'unknown'
    reasons = [entry['code'] for entry in limitations] + document_reasons
    if not prices:
        reasons.append('TENDER_ECONOMICS_INCOMPLETE')
    if schedule != 'complete':
        reasons.append('DELIVERY_SCHEDULE_UNKNOWN')
    return {'requirements': requirements, 'economics': 'complete' if prices else 'unknown',
            'schedule': schedule, 'complete': requirements == 'complete' and prices and schedule == 'complete',
            'reasons': sorted(set(reasons))}


def enrich_from_documents(tender: dict, documents: list[dict]) -> dict:
    terms = dict(tender.get('terms') or {})
    terms.setdefault('evidence', [])
    deadlines = [terms['delivery_deadline']] if terms.get('delivery_deadline') else []
    date_labels = {'Поставка до': 'delivery_deadline', 'Начало производства': 'start_at',
                   'Данные о расходах подтверждены': 'costs_confirmed_at'}
    money_labels = {'Логистика, RUB': 'logistics_cost', 'Прочие обязательные расходы, RUB': 'other_cost'}
    # These are explicit labelled facts. Unstructured prose remains unknown for model-assisted extraction.
    found = {}
    for document in documents:
        for line in document['text'].splitlines():
            label, sep, raw = line.partition(': ')
            if not sep:
                continue
            value, key = None, None
            if label in date_labels:
                key, value = date_labels[label], _iso(raw.strip())
            elif label in money_labels and re.fullmatch(r'\d{1,20}(?:[.,]\d{1,8})?', raw.strip()):
                key, value = money_labels[label], raw.strip().replace(',', '.')
            elif label == 'Срок доставки, дней' and re.fullmatch(r'\d{1,3}', raw.strip()):
                key, value = 'delivery_days', int(raw.strip())
            elif label == 'Налоговая база' and raw.strip() == 'цены и затраты сопоставимы':
                key, value = 'tax_comparable', True
            if key and value is not None:
                found.setdefault(key, set()).add(value)
                terms['evidence'].append({'path': document['url'], 'quote': line})
    for key, values in found.items():
        if len(values) == 1:
            terms[key] = next(iter(values))
        else:
            terms[key] = None
        if key in {'logistics_cost', 'other_cost'}:
            terms[key.replace('_cost', '_currency')] = 'RUB'
    if terms.get('delivery_deadline') and len(tender.get('source_card', {}).get('Lots') or []) == 1:
        terms['source_schedule_complete'] = True
    if deadlines and terms.get('delivery_deadline') and any(d != terms['delivery_deadline'] for d in deadlines):
        terms['delivery_deadline'] = None
    tender['terms'] = terms
    tender['documents'] = documents
    return tender
