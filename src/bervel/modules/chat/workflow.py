"""Server-owned phase rules and stable, bounded evidence; no model or persistence calls."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

from bervel.core.errors import CoreError


PHASES = ('research', 'synthesizing', 'generating', 'verifying', 'completed', 'partial', 'blocked')
TERMINAL = frozenset({'completed', 'partial', 'blocked'})
PUBLIC_FIELDS = ('phase', 'updated_at', 'reason', 'research_rounds', 'max_research_rounds',
    'web_searches', 'max_web_searches', 'elapsed_seconds', 'max_research_seconds', 'history')
READS = frozenset({'file.list', 'file.latest_ready', 'file.read', 'file.read_page', 'file.query', 'file.download',
    'file.knowledge', 'knowledge.search', 'settings.read', 'ticket.read', 'ticket.list'})


def timestamp(now=None) -> str:
    return now.isoformat() if isinstance(now, datetime) else now or datetime.now(timezone.utc).isoformat()


def initial_workflow(now=None, *, unlimited_research: bool = False) -> dict:
    at = timestamp(now)
    return {'version': 0, 'phase': 'research', 'updated_at': at, 'reason': None,
        'research_rounds': 0, 'max_research_rounds': None if unlimited_research else 4, 'web_searches': 0, 'max_web_searches': 0 if unlimited_research else 2,
        'elapsed_seconds': 0, 'max_research_seconds': None if unlimited_research else 240, 'unlimited_research': unlimited_research,
        'history': [{'phase': 'research', 'at': at, 'reason': None}], 'goal': None,
        'evidence': {'entries': [], 'coverage': {'total_entries': 0, 'shown_entries': 0, 'truncated': False}}}


def public_workflow(value: dict | None) -> dict | None:
    if not value:
        return None
    result = {key: deepcopy(value[key]) for key in PUBLIC_FIELDS}
    result['history'] = [{key: entry.get(key) for key in ('phase', 'at', 'reason')} for entry in value['history']]
    return result


def validate_transition(previous: dict | None, following: dict) -> None:
    if not isinstance(following, dict) or following.get('phase') not in PHASES:
        raise CoreError('VALIDATION_ERROR', 'Некорректный этап задания.')
    expected = previous.get('version', -1) + 1 if previous else 0
    if type(following.get('version')) is not int or following['version'] != expected:
        raise CoreError('STALE_ATTEMPT', 'Версия этапа задания изменилась.')
    if previous:
        old, new = previous['phase'], following['phase']
        if old in TERMINAL:
            raise CoreError('STALE_ATTEMPT', 'Задание уже завершено.')
        if PHASES.index(new) < PHASES.index(old):
            raise CoreError('VALIDATION_ERROR', 'Возврат к предыдущему этапу запрещён.')
        if old == 'research' and new == 'generating' and (following.get('goal') or {}).get('deliverable') == 'file':
            raise CoreError('VALIDATION_ERROR', 'Документ требует подготовки текста перед созданием.')
    for name in ('research_rounds', 'max_research_rounds', 'web_searches', 'max_web_searches', 'elapsed_seconds', 'max_research_seconds'):
        if name in {'max_research_rounds', 'max_research_seconds'} and following.get(name) is None and following.get('unlimited_research') is True:
            continue
        if not isinstance(following.get(name), (int, float)) or isinstance(following[name], bool) or following[name] < 0:
            raise CoreError('VALIDATION_ERROR', 'Некорректный бюджет задания.')


def advance(value: dict, phase: str | None = None, reason: str | None = None, **changes) -> dict:
    following = {**deepcopy(value), **changes, 'version': value['version'] + 1, 'updated_at': timestamp()}
    if phase and phase != value['phase']:
        following.update(phase=phase, reason=reason)
        following['history'].append({'phase': phase, 'at': following['updated_at'], 'reason': reason})
    elif reason is not None:
        following['reason'] = reason
    validate_transition(value, following)
    return following


def research_exhausted(value: dict) -> bool:
    if value.get('unlimited_research') is True:
        return False
    return any(value[used] >= value[maximum] for used, maximum in (
        ('research_rounds', 'max_research_rounds'), ('web_searches', 'max_web_searches'),
        ('elapsed_seconds', 'max_research_seconds')) if value[maximum] is not None)


def _size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())


def remember(value: dict, result: dict, operation: str, ordinal: int) -> None:
    """An appended receipt never takes space away from earlier evidence."""
    notebook = value['evidence']
    entries, coverage = notebook['entries'], notebook['coverage']
    candidates = []
    if result['kind'] == 'web':
        answer = result['answer']
        cited = [source for source in result['sources'] if source['url'] in answer]
        selected = cited or result['sources'][:12]
        omitted = len(selected) < len(result['sources'])
        candidates.append({'key': 'web:' + str(ordinal), 'kind': 'web', 'text': answer,
            'sources': selected, 'truncated': omitted,
            'source_coverage': {'total': len(result['sources']), 'shown': len(selected), 'truncated': omitted}})
    elif result['kind'] == 'data':
        data = result.get('data', {})
        for source in data.get('sources', []):
            if source.get('status') == 'metadata_only' or source.get('unit') == 'metadata':
                continue
            key = hashlib.sha256(json.dumps([source['artifact_id'], source.get('unit'), source.get('ordinal'),
                source['row_start'], source['row_end']], ensure_ascii=False).encode()).hexdigest()
            candidates.append({'key': key, 'kind': 'file', 'text': source['excerpt'], 'sources': [{
                name: source[name] for name in ('artifact_id', 'filename', 'unit', 'row_start', 'row_end', 'ordinal',
                    'total_rows', 'is_mock', 'notice', 'truncated') if name in source}], 'truncated': source.get('truncated', False)})
        if operation == 'file.query':
            candidates.append({'key': 'query:' + str(ordinal), 'kind': 'file', 'text': json.dumps({
                name: field for name, field in data.items() if name != 'sources'}, ensure_ascii=False),
                'sources': data.get('sources', []), 'truncated': False})
    for entry in candidates:
        existing = next((item for item in entries if item['key'] == entry['key']), None)
        if existing and len(existing['text']) >= len(entry['text']):
            continue
        coverage['total_entries'] += int(existing is None)
        limit = 32 * 1024 if entry['kind'] == 'web' else 64 * 1024
        used = sum(_size(item) for item in entries if item['kind'] == entry['kind'] and item is not existing)
        available = limit - used - _size({**entry, 'text': ''}) - 16
        if available < 256:
            coverage['truncated'] = True
            continue
        original = entry['text']
        low, high, kept = 0, len(original), ''
        while low <= high:
            middle = (low + high) // 2
            if _size({**entry, 'text': original[:middle]}) <= limit - used - 16:
                kept, low = original[:middle], middle + 1
            else:
                high = middle - 1
        entry['text'] = kept
        entry['truncated'] = entry['truncated'] or len(kept) < len(original)
        coverage['truncated'] = coverage['truncated'] or entry['truncated']
        if existing:
            entries[entries.index(existing)] = entry
        else:
            entries.append(entry)
    coverage['shown_entries'] = len(entries)


def evidence_context(value: dict) -> dict:
    return deepcopy(value['evidence'])
