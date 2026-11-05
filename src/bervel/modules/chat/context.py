"""Bounded, explicit model-only projections; durable results are never modified."""
import json

from bervel.core.errors import CoreError

PLANNER_MAX_BYTES = 192 * 1024


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def size(value) -> int:
    return len(encoded(value).encode('utf-8'))


IDENTITY_FIELDS = ('kind', 'operation', 'run_id', 'ticket_id', 'artifact_id', 'revision', 'id', 'number',
    'state', 'expected_revision', 'format', 'usage_role', 'actual', 'decision', 'analysis_complete', 'counts', 'coverage', 'funnel', 'row_start', 'row_end',
    'total_rows', 'unit', 'status', 'truncated', 'title', 'reason', 'filename', 'created_at', 'is_mock', 'notice',
    'complete', 'count', 'count_unit', 'sums', 'group_by', 'total', 'shown', 'offset', 'next_offset', 'sources')


def bounded(value, budget: int):
    """A partial value has explicit coverage; text is never cut into malformed JSON or UTF-8."""
    if size(value) <= budget: return value
    if budget < 128: return None
    if isinstance(value, str):
        raw = value.encode('utf-8')
        low, high = 0, len(raw)
        result = {'text': '', '_context': {'total_bytes': len(raw), 'shown_bytes': 0, 'truncated': True}}
        while low <= high:
            middle = (low+high)//2
            text = raw[:middle].decode('utf-8', errors='ignore')
            candidate = {'text': text, '_context': {'total_bytes': len(raw), 'shown_bytes': len(text.encode()), 'truncated': True}}
            if size(candidate) <= budget: result, low = candidate, middle+1
            else: high = middle-1
        return result
    if isinstance(value, list):
        result = {'items': [], '_context': {'total': len(value), 'shown': 0, 'truncated': True}}
        for item in value:
            remaining = budget-size(result)-24
            if remaining < 256: break
            item = bounded(item, min(remaining, max(256, budget//4)))
            if size({**result, 'items': [*result['items'], item]}) > budget: break
            result['items'].append(item)
            result['_context']['shown'] += 1
        return result
    if isinstance(value, dict):
        result = {'_context': {'total_fields': len(value), 'shown_fields': 0, 'truncated': True}}
        names = [name for name in IDENTITY_FIELDS if name in value] + [name for name in value if name not in IDENTITY_FIELDS]
        for name in names:
            remaining = budget-size(result)-size(name)-8
            if remaining < 256: break
            item = bounded(value[name], min(remaining, max(256, budget//4)))
            if size({**result, name: item}) > budget: continue
            result[name] = item
            result['_context']['shown_fields'] += 1
        return result
    # Numeric/boolean/null values normally fit; this only handles an impractically large integer.
    return {'_context': {'truncated': True, 'total_bytes': size(value)}}


def with_receipt(result: dict, operation: str, arguments: dict, ordinal: int) -> dict:
    """Model-only acknowledgement of the exact completed operation, without duplicating its full result."""
    data = result.get('data') if isinstance(result.get('data'), dict) else {}
    identity = {name: result.get(name, data.get(name)) for name in
        ('kind', 'artifact_id', 'ticket_id', 'run_id', 'revision', 'state', 'status') if name in result or name in data}
    return {**result, '_receipt': {'operation': operation, 'state': 'completed', 'ordinal': ordinal,
        'arguments': arguments, 'result_ref': identity}}


def failed_receipt(operation: str, arguments: dict, ordinal: int, error: CoreError) -> dict:
    """A rejection is planning context, never a successful result card or source."""
    return {'error': {'code': error.code, 'message': error.message}, '_receipt': {
        'operation': operation, 'arguments': arguments, 'ordinal': ordinal, 'state': 'failed', 'result_ref': {}}}


def result_projection(result: dict, budget: int) -> dict:
    """Reserve acknowledgement identity before allocating space to potentially large result details."""
    original = {key: value for key, value in result.items() if key!='_receipt'}
    receipt = result.get('_receipt')
    if receipt is not None:
        if budget<1024:
            raise CoreError('ASSISTANT_CONTEXT_LIMIT', 'Квитанция действия не помещается в контекст. Уточните нужный результат.')
        allowance = min(4096, budget//2)
        if size(receipt)>allowance:
            receipt = {key: receipt[key] for key in ('operation', 'state', 'ordinal', 'result_ref')}
            receipt['arguments'] = bounded(result['_receipt']['arguments'], max(128, allowance-size(receipt)-32))
        budget -= size(receipt)+32
    if original.get('kind') == 'data' and original.get('operation') == 'file.read_page':
        projected = file_page_projection(original, budget)
    else:
        projected = bounded(original, budget)
    return {**projected, '_receipt': receipt} if receipt is not None else projected


def file_page_projection(result: dict, budget: int) -> dict:
    """A model-only cut must update paging counters, without altering the saved complete page."""
    if size(result) <= budget: return result
    data = result['data']
    offset = data['offset']
    metadata = {key: data[key] for key in ('artifact_id', 'filename', 'media_type', 'usage_role', 'is_mock', 'notice',
        'status', 'source_complete', 'pagination_unit', 'total', 'offset', 'limit') if key in data}
    arguments = {'artifact_id': data['artifact_id'], 'offset': offset, 'limit': data.get('limit', 20)}
    output = {'kind': 'data', 'operation': 'file.read_page', 'data': {**metadata, 'shown': 0, 'next_offset': offset,
        'truncated': True, 'sources': [], 'read_next': {'operation': 'file.read_page', 'arguments': arguments}},
        '_context': {'truncated': True, 'original_bytes': size(result)}}
    target = output['data']
    for source in data['sources']:
        remaining = budget-size(output)-128
        if remaining < 768: break
        if size(source) > remaining: break
        target['sources'].append(source)
        target['shown'] += 1
    target['next_offset'] = offset+target['shown'] if offset+target['shown'] < data['total'] else None
    arguments['offset'] = target['next_offset'] if target['next_offset'] is not None else offset
    if size(output) > budget:
        raise CoreError('ASSISTANT_CONTEXT_LIMIT', 'Страница файла не помещается в контекст. Запросите меньше фрагментов.')
    return output


def model_payload(question: str, history: list[dict], sources: list[dict], catalog: list[dict], results: list[dict], *, budget: int) -> dict:
    previous = [{'question': turn['question'], 'answer': turn.get('answer')} for turn in history if turn['state']=='completed']
    values = {'history': previous[-6:], 'sources': sources, 'catalog': catalog, 'results': results}
    totals = {'history': len(previous), 'sources': len(sources), 'catalog': len(catalog), 'results': len(results)}
    keys = {'history': 'HISTORY_UNTRUSTED', 'sources': 'CURRENT_DOCUMENTS', 'catalog': 'CATALOG_UNTRUSTED', 'results': 'ACTION_RESULTS'}
    payload = {'CURRENT_USER_REQUEST': question, **{key: [] for key in keys.values()}, 'CONTEXT_COVERAGE': {}}
    available = budget-size(payload)-2048
    if available<4096:
        raise CoreError('ASSISTANT_CONTEXT_LIMIT', 'Запрос не помещается в контекст; сократите его.')
    weights = {'history': 1, 'sources': 4, 'catalog': 1, 'results': 8}
    for section, entries in values.items():
        allowance = available*weights[section]//sum(weights.values())
        output, partial = [], len(entries)!=totals[section]
        # Every executed result remains represented, including the newest result after repeated reads.
        per_result = max(256, allowance//max(1,len(entries))-96) if section=='results' else max(1024, allowance//4)
        for item in entries:
            remaining = allowance-size(output)-32
            if remaining<256: break
            cap = min(remaining, per_result)
            projected = result_projection(item, cap) if section=='results' else bounded(item, cap)
            if section=='sources' and projected!=item and isinstance(projected, dict):
                projected = {**projected, 'truncated': True}
            if size([*output, projected])>allowance: break
            output.append(projected); partial = partial or projected!=item
        coverage = {'total': totals[section], 'shown': len(output), 'truncated': partial or len(output)<totals[section]}
        if section=='catalog' and coverage['truncated']:
            coverage['read_next'] = {'operation': 'file.list', 'arguments': {'cursor': len(output), 'limit': 100}}
        if section=='sources' and coverage['truncated']:
            coverage['details'] = 'Use file.read_page for ordered detail, file.query for exact CSV/XLSX totals, or file.download for the original; use the exact artifact_id.'
        payload[keys[section]] = output
        payload['CONTEXT_COVERAGE'][section] = coverage
    if size(payload)>budget:
        raise CoreError('ASSISTANT_CONTEXT_LIMIT', 'Контекст превышает безопасный объём. Уточните нужный отчёт или файл.')
    return payload
