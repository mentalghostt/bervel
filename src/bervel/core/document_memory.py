"""Scoped retrieval over any registered original; unsupported content stays explicit."""
import base64
from decimal import Decimal, localcontext
import heapq
import json
import re
from typing import Protocol
from uuid import UUID

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, ArtifactRef
from bervel.core.services import ArtifactService


class DocumentReader(Protocol):
    def extract(self, content: bytes, filename: str, media_type: str) -> dict: ...
    def table(self, content: bytes, filename: str, media_type: str, *, sheet: str | None = None) -> dict: ...


class DocumentIndex(Protocol):
    def document_index(self, snapshot: dict) -> dict | None: ...
    def save_document_index(self, snapshot: dict, result: dict) -> None: ...


def words(value: str) -> set[str]:
    return set(re.findall(r'\w+', value.casefold()))


def unreadable() -> dict:
    return {'status': 'metadata_only', 'chunks': [{'row_start': 0, 'row_end': 0, 'total_rows': 0,
        'unit': 'metadata', 'excerpt': 'Содержимое недоступно: оригинал не прочитан или не прошёл проверку целостности.'}]}


def offer(heap: list, candidate: tuple) -> None:
    if len(heap) < 12:
        heapq.heappush(heap, candidate)
    elif candidate[:3] > heap[0][:3]:
        heapq.heapreplace(heap, candidate)


def provenance(metadata: dict, *, is_mock: bool = False) -> dict:
    identity = {key: metadata[key] for key in ('artifact_id', 'filename', 'created_at', 'media_type', 'usage_role') if key in metadata}
    mock = (is_mock or metadata.get('source', {}).get('origin_system') == 'bervel-assistant-mock'
        or bool(re.search(r'mock|демонстрац|демо', metadata['filename'], re.IGNORECASE)))
    return {**identity, 'is_mock': mock, 'notice': 'Демонстрационные данные (mock); не фактический склад.' if mock else None}


class DocumentMemory:
    def __init__(self, artifacts: ArtifactService, repository: DocumentIndex, reader: DocumentReader, *, knowledge=None):
        self.artifacts, self.repository, self.reader = artifacts, repository, reader
        self.knowledge=knowledge

    def _index(self, original) -> dict:
        item = original.metadata
        index = self.repository.document_index(item)
        table = item['filename'].casefold().endswith(('.csv', '.xlsx')) or item['media_type'] in {'text/csv', 'application/csv'}
        if index is None or (table and 'is_mock' not in index):
            index = self.reader.extract(original.content, item['filename'], item['media_type'])
            self.repository.save_document_index(item, index)
        return index

    def source_metadata(self, original) -> dict:
        """Classify explicit table provenance once; old derived caches are refreshed without touching vectors."""
        item = original.metadata
        table = item['filename'].casefold().endswith(('.csv', '.xlsx')) or item['media_type'] in {'text/csv', 'application/csv'}
        return provenance(item, is_mock=self._index(original).get('is_mock', False) if table else False)

    def search(self, actor: ActorContext, query: str, *, usage_role: str | None = None, limit: int = 8) -> dict:
        """Discover in the current accessible catalog, rather than requiring an upload filename."""
        catalog, cursor = [], 0
        while cursor is not None:
            page = self.artifacts.list(actor, cursor, 100)
            catalog.extend(item for item in page['artifacts'] if usage_role is None or item.get('usage_role') == usage_role)
            cursor = page['next_cursor']
        if self.knowledge is not None:
            retrieval = self.knowledge.retrieve_result(actor, catalog, query)
            candidates, retrieval_mode, retrieval_notice, retrieval_reason = retrieval.sources, retrieval.mode, retrieval.notice, retrieval.reason
        else:
            candidates = self.context(actor, catalog, query)
            retrieval_mode, retrieval_notice, retrieval_reason = 'lexical', None, None
        # Prefer confirmed lexical/name hits when available. Semantic candidates remain explicitly provisional.
        lexical = [source for source in candidates if source.get('named') or source.get('lexical_match')
            or (self.knowledge is None and words(query) & words(source.get('excerpt', '')+' '+source['filename']))]
        if lexical: candidates = lexical
        elif self.knowledge is not None and retrieval_mode == 'lexical': candidates = []
        elif self.knowledge is None and usage_role is None: candidates = []
        metadata = {item['artifact_id']: item for item in catalog}
        matches, sources, seen = [], [], set()
        for source in candidates:
            identifier = source['artifact_id']
            if identifier not in metadata: continue
            if identifier not in seen:
                if len(matches) >= limit: continue
                # A cached hit must never disclose bytes when its original is unavailable or corrupted.
                original = self.artifacts.read(actor, ArtifactRef(UUID(identifier)))
                item = original.metadata
                metadata[identifier] = self.source_metadata(original)
                matches.append({**metadata[identifier], 'processing_state': item.get('processing_state'),
                    'match': 'lexical' if lexical else 'role' if usage_role else 'semantic_candidate'})
                seen.add(identifier)
            if sum(s['artifact_id'] == identifier for s in sources) >= 2: continue
            notices = list(dict.fromkeys(value for value in (metadata[identifier].get('notice'), source.get('notice'), retrieval_notice) if value))
            sources.append({**source, **metadata[identifier], 'excerpt': source['excerpt'][:1500],
                'truncated': True, 'retrieval': 'search_candidate', 'retrieval_mode': retrieval_mode,
                'notice': ' '.join(notices) or None})
        return {'query': query, 'usage_role': usage_role, 'catalog_total': len(catalog), 'shown': len(matches),
            'retrieval_complete': False, 'matches': matches, 'sources': sources,
            'retrieval_mode': retrieval_mode, 'retrieval_reason': retrieval_reason,
            'notice': ' '.join(value for value in (retrieval_notice,
                'Поиск показывает кандидатов и отдельные фрагменты, не полный состав базы и не полные числовые итоги.') if value)}

    def read_page(self, actor: ActorContext, reference: ArtifactRef, *, offset: int = 0, limit: int = 20) -> dict:
        original = self.artifacts.read(actor, reference)
        item = original.metadata
        index = self._index(original)
        identity = provenance(item, is_mock=index.get('is_mock', False))
        chunks = index['chunks']
        selected = chunks[offset:offset+limit]
        next_offset = offset+len(selected) if offset+len(selected) < len(chunks) else None
        partial = index['status'] != 'ready' or offset > 0 or next_offset is not None
        sources = [{**identity, **chunk, 'ordinal': offset+ordinal, 'status': index['status'],
            'truncated': partial, 'retrieval': 'page'} for ordinal, chunk in enumerate(selected)]
        return {**identity, 'pagination_unit': 'chunk', 'status': index['status'], 'source_complete': index['status'] == 'ready',
            'offset': offset, 'limit': limit, 'total': len(chunks), 'shown': len(sources), 'next_offset': next_offset,
            'truncated': partial, 'sources': sources}

    def query(self, actor: ActorContext, reference: ArtifactRef, *, sum_columns: list[str] | None = None,
              group_by: str | None = None, sheet: str | None = None) -> dict:
        original = self.artifacts.read(actor, reference)
        table = self.reader.table(original.content, original.metadata['filename'], original.metadata['media_type'], sheet=sheet)
        columns, rows = table['columns'], table['rows']
        if (not columns or any(not isinstance(name, str) or not name.strip() for name in columns)
                or len(set(columns)) != len(columns) or any(len(row) != len(columns) for row in rows)):
            raise CoreError('VALIDATION_ERROR', 'Для точных итогов нужны уникальные непустые заголовки и одинаковое число ячеек в строках.')
        sums = list(dict.fromkeys(sum_columns or []))
        if any(name not in columns for name in [*sums, *([group_by] if group_by else [])]):
            raise CoreError('VALIDATION_ERROR', 'Указанный столбец не найден. Используйте точное имя из заголовка таблицы.')
        def number(value):
            text = str(value).strip()
            # Decimal receives only unambiguous finite decimal notation; blanks/formulas/errors are unknown, never zero.
            if len(text) > 100 or not re.fullmatch(r'[+-]?\d+(?:[.,]\d+)?', text, re.ASCII):
                raise CoreError('FILE_QUERY_NON_NUMERIC', 'В столбце суммы есть пустые, нечисловые, неоднозначные ячейки или формулы; точный итог не вычислялся.')
            return Decimal(text.replace(',', '.'))
        totals, groups = {name: Decimal(0) for name in sums}, {}
        with localcontext() as precision:
            precision.prec = 256
            for row in rows:
                values = {name: number(row[columns.index(name)]) for name in sums}
                for name, value in values.items(): totals[name] += value
                if group_by:
                    value = row[columns.index(group_by)]
                    if isinstance(value, dict):
                        raise CoreError('FILE_QUERY_NON_NUMERIC', 'Столбец группировки содержит формулу или ошибку; группа неизвестна.')
                    label = '' if value is None else str(value)
                    group = groups.setdefault(label, {'count': 0, 'sums': {name: Decimal(0) for name in sums}})
                    group['count'] += 1
                    for name, value in values.items(): group['sums'][name] += value
        mock_columns = [index for index, name in enumerate(columns) if name.strip().casefold() == 'data_kind']
        identity = provenance(original.metadata, is_mock=any(str(row[index]).strip().casefold() == 'mock' for row in rows for index in mock_columns))
        result = {**identity, 'sheet': table['sheet'], 'columns': columns,
            'count': len(rows), 'sums': {name: format(value, 'f') for name, value in totals.items()}, 'group_by': group_by,
            'groups': [{'value': value, 'count': group['count'], 'sums': {name: format(amount, 'f') for name, amount in group['sums'].items()}}
                for value, group in sorted(groups.items())], 'complete': True,
            'count_unit': 'nonempty_data_rows' if table['sheet'] is not None else 'data_rows',
            'numeric_policy': 'Exact decimal values; no unit conversion; unknown cells are rejected.'}
        result['sources'] = [{**identity, 'unit': 'row', 'sheet': table['sheet'], 'row_start': table['row_start'],
            'row_end': table['row_end'], 'total_rows': table['total_rows'],
            'status': 'ready', 'truncated': False, 'retrieval': 'exact_table',
            'excerpt': 'Полная таблица'+(' / лист '+table['sheet'] if table['sheet'] else '')+': '+json.dumps(
                {'count': result['count'], 'sums': result['sums'], 'group_by': group_by}, ensure_ascii=False)}]
        return result

    def context(self, actor: ActorContext, snapshot: list[dict] | dict | None, question: str, *, complete: bool = False) -> list[dict]:
        catalog = [snapshot] if isinstance(snapshot, dict) else snapshot or []
        if not catalog:
            return []
        if self.knowledge is not None:
            return self.indexed_context(actor,catalog,question,complete=complete)
        query = words(question)
        candidates, representatives, complete_sources = [], [], []
        counts, verified = {}, set()
        for position, item in enumerate(catalog):
            ref = ArtifactRef(UUID(item['artifact_id']))
            index = self.repository.document_index(item)
            if index is None:
                try:
                    original = self.artifacts.read(actor, ref)
                except CoreError as error:
                    if error.code != 'TRANSIENT_DEPENDENCY_ERROR': raise
                    index = unreadable()
                else:
                    index = self.reader.extract(original.content, item['filename'], original.metadata['media_type'])
                    self.repository.save_document_index(item, index)
                verified.add((item['artifact_id']))
            chunks = index['chunks']
            counts[item['artifact_id']] = len(chunks)
            filename_score = len(query & words(item['filename'])) * 8
            if item['filename'].casefold() in question.casefold():
                filename_score += 100
            best = None
            for ordinal, chunk in enumerate(chunks):
                score = filename_score + len(query & words(chunk['excerpt']))
                source = {**{key: item[key] for key in ('artifact_id', 'filename', 'created_at')},
                          **{key: chunk[key] for key in ('row_start', 'row_end', 'total_rows', 'unit', 'excerpt')},
                          'media_type': item.get('media_type', 'application/octet-stream'),
                          'status': index['status'], 'truncated': index['status'] == 'partial'}
                if complete:
                    complete_sources.append(source)
                    continue
                candidate = (score, -position, -ordinal, source)
                offer(candidates, candidate)
                if best is None or candidate[:3] > best[:3]: best = candidate
            if best is not None: offer(representatives, best)
        rank = lambda value: (-value[0], -value[1], -value[2])
        representatives.sort(key=rank)
        candidates.sort(key=rank)
        selected, seen, budget = [], set(), 16000
        # Reserve a fragment per relevant file before filling with extra chunks.
        for group in (representatives, candidates):
            for number, candidate in enumerate(group):
                _, position, ordinal, source = candidate
                if (position, ordinal) in seen or len(selected) >= 12 or not budget: continue
                allowance = min(3500, budget // (len(group) - number)) if group is representatives else min(3500, budget)
                if not allowance: continue
                source = {**source, 'excerpt': source['excerpt'][:allowance],
                          'truncated': source['truncated'] or len(source['excerpt']) > allowance}
                selected.append(source); seen.add((position, ordinal)); budget -= len(source['excerpt'])
        coverage = {}
        for source in selected:
            coverage[source['artifact_id']] = coverage.get(source['artifact_id'], 0) + 1
        for source in selected:
            source['truncated'] = source['truncated'] or coverage[source['artifact_id']] < counts[source['artifact_id']]
        if complete:
            selected = complete_sources
        for artifact_id in {s['artifact_id'] for s in selected}:
            if artifact_id in verified: continue
            source = next(source for source in selected if source['artifact_id'] == artifact_id)
            try:
                self.artifacts.read(actor, ArtifactRef(UUID(artifact_id)))
            except CoreError as error:
                if error.code != 'TRANSIENT_DEPENDENCY_ERROR': raise
                # Never send stale cached content when its original cannot be verified.
                replacement = {**source, **unreadable()['chunks'][0], 'status': 'metadata_only', 'truncated': False}
                selected = [s for s in selected if s['artifact_id'] != artifact_id] + [replacement]
        if complete:
            if not selected or any(s['status'] != 'ready' or s['truncated'] or s['unit'] == 'metadata' for s in selected):
                raise CoreError('RAG_PROFILE_INCOMPLETE', 'Профиль продукции не удалось прочитать целиком. Выберите полностью читаемый CSV/TXT/XLSX/DOCX/PDF.')
            if sum(len(s['excerpt'].encode('utf-8')) for s in selected) > 96 * 1024:
                raise CoreError('RAG_PROFILE_INCOMPLETE', 'Профиль продукции превышает 96 КиБ текста для прямой оценки. Выберите компактный профиль; частичный отбор не выполнялся.')
        return selected

    def indexed_context(self,actor,catalog,question,*,complete=False):
        retrieval=self.knowledge.retrieve_result(actor,catalog,question,complete=complete)
        sources=retrieval.sources
        selected=[]; budget=16000
        for source in sources:
            if not complete and (len(selected)>=12 or len(source['excerpt'])>budget): continue
            selected.append({**source,'truncated':source.get('status')=='partial',
                'retrieval':'complete' if complete else retrieval.mode,'retrieval_mode':retrieval.mode})
            budget-=len(source['excerpt'])
        counts={}
        for source in selected:
            key=(source['artifact_id']); counts[key]=counts.get(key,0)+1
        if complete and set(counts)!={s['artifact_id'] for s in catalog}:
            raise CoreError('RAG_PROFILE_INCOMPLETE','Одна из выбранных файлов отсутствует в полном индексе; профиль не использован частично.')
        for key in counts:
            ref=ArtifactRef(UUID(key))
            try: self.artifacts.read(actor,ref)
            except CoreError as error:
                if error.code!='TRANSIENT_DEPENDENCY_ERROR': raise
                first=next(s for s in selected if s['artifact_id']==key)
                selected=[s for s in selected if s['artifact_id']!=key]+[
                    {**first,**unreadable()['chunks'][0],'status':'metadata_only','truncated':False}]
                continue
            total=self.knowledge.status(actor,ref)['total_chunks']
            for source in selected:
                if (source['artifact_id'])==key:
                    source['truncated']=source['truncated'] or counts[key]<total
        if complete:
            if not selected or any(s.get('status')!='ready' or s['truncated'] or s['unit']=='metadata' for s in selected):
                raise CoreError('RAG_PROFILE_INCOMPLETE','Профиль продукции не удалось прочитать целиком. Проверьте состояние индекса.')
            if sum(len(s['excerpt'].encode()) for s in selected)>96*1024:
                raise CoreError('RAG_PROFILE_INCOMPLETE','Профиль продукции превышает 96 КиБ текста для прямой оценки. Выберите компактный профиль.')
        return selected

    def images(self, actor: ActorContext, sources: list[dict]) -> list[dict]:
        images, seen, remaining = [], set(), 5 * 1024 * 1024
        for source in sources:
            if source.get('unit') == 'image': source['image_attached'] = False
            if source.get('unit') != 'image' or source['artifact_id'] in seen or len(images) >= 2:
                continue
            original = self.artifacts.read(actor, ArtifactRef(UUID(source['artifact_id'])))
            content = original.content
            if content.startswith(b'\x89PNG\r\n\x1a\n'): media = 'image/png'
            elif content.startswith(b'\xff\xd8\xff'): media = 'image/jpeg'
            elif content.startswith(b'RIFF') and content[8:12] == b'WEBP': media = 'image/webp'
            else: continue
            if len(content) > remaining: continue
            images.append({'media_type': media, 'data': base64.b64encode(content).decode('ascii'),
                           'label': f"{source['filename']} (artifact {source['artifact_id']})"})
            remaining -= len(content); seen.add(source['artifact_id'])
            source['image_attached'] = True
        return images
