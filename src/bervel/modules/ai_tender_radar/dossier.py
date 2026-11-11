"""Addressable, read-only snapshots for one tender's bounded model research."""
from copy import deepcopy
import json

from bervel.core.errors import CoreError


PAGE_CHARS = 16000


def _invalid(message: str) -> None:
    raise CoreError('MODEL_INVALID_OUTPUT', message)


def _text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False)


class TenderDossier:
    """No storage, URLs or model-supplied identities can cross this boundary."""

    def __init__(self, card: dict, stock: dict | None, profile: dict | None, history: list | None):
        self.sources = deepcopy({'card:': card, 'stock:': stock or {}, 'profile:': profile or {},
                                 'history:': history or []})
        self.sources['documents:'] = []
        for index, document in enumerate((card.get('tender') or {}).get('documents') or []):
            self.sources[f'document:{index}'] = deepcopy(document.get('text') or '')
            self.sources['documents:'].append({'ref': f'document:{index}', 'filename': document.get('filename'),
                'url': document.get('url'), 'characters': len(document.get('text') or '')})

    def resolve(self, ref: str):
        if not isinstance(ref, str) or len(ref) > 1000:
            _invalid('Некорректная ссылка на данные тендера.')
        root, separator, pointer = ref.partition('/')
        if root not in self.sources:
            _invalid('Запрошенного источника нет в досье этой карточки.')
        value = self.sources[root]
        if separator:
            for part in pointer.split('/'):
                key = part.replace('~1', '/').replace('~0', '~')
                if isinstance(value, dict) and key in value:
                    value = value[key]
                elif isinstance(value, list) and key.isdecimal() and int(key) < len(value):
                    value = value[int(key)]
                else:
                    _invalid('Ссылка указывает на отсутствующее поле досье.')
        return value

    def contains(self, ref: str) -> bool:
        try:
            self.resolve(ref)
            return True
        except CoreError:
            return False

    def preview(self, ref: str, budget: int = 5000):
        source = self.resolve(ref)
        if len(_text(source)) <= budget:
            return deepcopy(source)
        remaining = [budget]

        def visit(value, path, depth):
            if remaining[0] <= 0 or depth > 4:
                return {'$ref': path, 'type': type(value).__name__}
            if isinstance(value, (dict, list)):
                count = len(value)
                rows = list(value.items())[:12] if isinstance(value, dict) else list(enumerate(value[:12]))
                children = {}
                for key, child in rows:
                    segment = str(key).replace('~', '~0').replace('/', '~1')
                    remaining[0] -= len(segment) + 30
                    children[str(key)] = visit(child, path + '/' + segment, depth + 1)
                if isinstance(value, list) or count > 12:
                    return {'$ref': path, 'count': count, 'preview': children}
                return children
            if isinstance(value, str) and (len(value) > 600 or len(value) > remaining[0]):
                remaining[0] -= 100
                return {'$ref': path, 'characters': len(value), 'excerpt': value[:80]}
            remaining[0] -= len(str(value))
            return value

        return visit(source, ref, 0)

    def catalog(self) -> dict:
        result = {root: self.preview(root) for root in self.sources if not root.startswith('document:') and root != 'card:'}
        result['card:'] = {'$ref': 'card:', 'fields': list(self.sources['card:'])}
        history_count = len(self.sources['history:'])
        result['history:'] = {'$ref': 'history:', 'count': history_count, 'recent': {
            str(index): self.preview(f'history:/{index}', budget=700)
            for index in range(max(0, history_count-6), history_count)}}
        return result

    def read(self, ref: str, offset: int, length: int) -> dict:
        value = _text(self.resolve(ref))
        if type(offset) is not int or not 0 <= offset <= len(value) or type(length) is not int or not 1 <= length <= PAGE_CHARS:
            _invalid('Некорректные границы порции источника.')
        end = min(len(value), offset + length)
        return {'ref': ref, 'offset': offset, 'content': value[offset:end], 'total_characters': len(value),
                'next_offset': end if end < len(value) else None}

    def search(self, ref: str, query: str, offset: int) -> dict:
        value = _text(self.resolve(ref))
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200 or type(offset) is not int or not 0 <= offset <= len(value):
            _invalid('Некорректный поиск по источнику.')
        # Exact text search keeps offsets stable for the subsequent paged read.
        matches, cursor = [], offset
        while len(matches) < 12:
            found = value.find(query, cursor)
            if found < 0:
                break
            matches.append({'offset': found, 'excerpt': value[max(0, found-120):found+len(query)+240]})
            cursor = found + len(query)
        return {'ref': ref, 'query': query, 'matches': matches,
                'next_offset': cursor if len(matches) == 12 else None, 'total_characters': len(value)}

    def add_refreshed(self, tender: dict) -> None:
        current = self.sources['card:'].get('tender') or {}
        if (not isinstance(tender, dict) or not current.get('id') or tender.get('id') != current['id']
                or bool(tender.get('simulated')) != bool(current.get('simulated'))):
            _invalid('Обновлённый источник не принадлежит текущей карточке.')
        self.sources['refreshed:'] = deepcopy(tender)
