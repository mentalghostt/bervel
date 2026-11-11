"""Presales application operations; storage, originals and execution are injected."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID

from bervel.core.errors import CoreError
from bervel.core.models import ArtifactRef
from bervel.core.services import authorize, validate_key
from bervel.modules.presales.settings import INITIAL_SETTINGS, validate_settings


def _page(cursor, limit):
    if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise CoreError('VALIDATION_ERROR', 'Некорректная страница результата.')


def _uuid(value):
    try:
        return value if isinstance(value, UUID) else UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise CoreError('VALIDATION_ERROR', 'Некорректный идентификатор.') from None


class PresalesService:
    def __init__(self, repository, artifacts, company, *, reader=None, model=None, renderer=None):
        self.repository = repository
        self.artifacts = artifacts
        self.company = company
        self.reader = reader
        self.model = model
        self.renderer = renderer

    def read_settings(self, actor):
        authorize(actor, 'settings:read')
        result = self.repository.read_settings(actor.data_scope)
        return ({'snapshot_id': result['snapshot_id'], 'settings': result['settings'], 'requires_save': False}
                if result else {'snapshot_id': None, 'settings': deepcopy(INITIAL_SETTINGS), 'requires_save': True})

    def save_settings(self, actor, base_snapshot_id, settings, command_key):
        authorize(actor, 'settings:write')
        validate_key(command_key)
        result = self.repository.save_settings(actor, _uuid(base_snapshot_id) if base_snapshot_id else None,
                                                validate_settings(settings), command_key)
        return {'snapshot_id': result['snapshot_id'], 'settings': result['settings'], 'requires_save': False}

    def _freeze(self, actor, sources):
        current = self.repository.read_settings(actor.data_scope)
        if current is None:
            raise CoreError('SETTINGS_REQUIRED', 'Сохраните видимые значения маржи в настройках пресейла перед анализом.')
        company = self.company.read_analysis_snapshot(actor)
        return {'sources': sources, 'company': company, 'settings': current['settings'],
                'settings_snapshot_id': current['snapshot_id'], 'evaluated_at': datetime.now(timezone.utc).isoformat(),
                'rule_version': 'presales-v1'}

    def create_request(self, actor, artifact_ids, command_key):
        for permission in ('process:start', 'artifact:read', 'artifact:write', 'settings:read'):
            authorize(actor, permission)
        validate_key(command_key)
        if not isinstance(artifact_ids, list) or not artifact_ids:
            raise CoreError('VALIDATION_ERROR', 'Выберите хотя бы один файл заявки.')
        ids = [str(_uuid(value)) for value in artifact_ids]
        if len(set(ids)) != len(ids):
            raise CoreError('VALIDATION_ERROR', 'Один файл нельзя выбрать дважды.')
        previous = self.repository.replay(actor, 'presales.request.create', command_key, {'artifact_ids': sorted(ids)})
        if previous is not None:
            return previous
        sources = []
        for artifact_id in ids:
            metadata = self.artifacts.metadata(actor, ArtifactRef(UUID(artifact_id)))
            sources.append({'artifact_id': artifact_id, 'filename': metadata['filename'], 'sha256': metadata['sha256']})
        return self.repository.create_request(actor, ids, self._freeze(actor, sources), command_key)

    def list_requests(self, actor, cursor=0, limit=30):
        authorize(actor, 'process:read')
        _page(cursor, limit)
        return self.repository.list_requests(actor.data_scope, cursor, limit)

    def read_request(self, actor, request_id):
        authorize(actor, 'process:read')
        return self.repository.read_request(actor.data_scope, _uuid(request_id))

    def read_revision(self, actor, request_id, revision_id):
        authorize(actor, 'process:read')
        return self.repository.revision_summary(actor.data_scope, _uuid(request_id), _uuid(revision_id))

    def list_lines(self, actor, request_id, revision_id, cursor=0, limit=50, *, list_id=None):
        authorize(actor, 'process:read')
        _page(cursor, limit)
        if list_id is not None:
            if not isinstance(list_id, str) or not list_id or len(list_id) > 128:
                raise CoreError('VALIDATION_ERROR', 'Некорректный идентификатор исходного списка.')
            return self.repository.read_source_lines(actor.data_scope, _uuid(request_id), _uuid(revision_id),
                                                      list_id, cursor, limit)
        return self.repository.read_lines(actor.data_scope, _uuid(request_id), _uuid(revision_id), cursor, limit)

    @staticmethod
    def _merge_decisions(base, changes, extracted, actor):
        if not isinstance(changes, dict) or set(changes) - {
                'primary_list_id', 'line_updates', 'approvals', 'client', 'requested_deadline'}:
            raise CoreError('VALIDATION_ERROR', 'Некорректные уточнения заявки.')
        result = deepcopy(base)
        all_lines = {line['line_id'] for line in extracted.get('lines', [])}
        all_lines.update(line['line_id'] for group in extracted.get('lists', []) for line in group.get('lines', []))
        if 'primary_list_id' in changes:
            if not isinstance(changes['primary_list_id'], str) or changes['primary_list_id'] not in {item['list_id'] for item in extracted.get('lists', [])}:
                raise CoreError('VALIDATION_ERROR', 'Основной список не найден в этой заявке.')
            if changes['primary_list_id'] != result.get('primary_list_id', extracted.get('primary_list_id')):
                result['approvals'], result['line_updates'] = {}, {}
            result['primary_list_id'] = changes['primary_list_id']
        for key in ('client', 'requested_deadline'):
            if key in changes:
                if changes[key] is not None and not isinstance(changes[key], str):
                    raise CoreError('VALIDATION_ERROR', 'Клиент и дата должны быть текстом.')
                if key == 'requested_deadline' and changes[key]:
                    try:
                        datetime.fromisoformat(changes[key].replace('Z', '+00:00'))
                    except ValueError:
                        raise CoreError('VALIDATION_ERROR', 'Укажите корректную дату готовности для клиента.') from None
                result[key] = changes[key]
        for group in ('line_updates', 'approvals'):
            patches = changes.get(group, {})
            if not isinstance(patches, dict):
                raise CoreError('VALIDATION_ERROR', 'Уточнения строк должны быть объектом.')
            result.setdefault(group, {})
            for line_id, patch in patches.items():
                if line_id not in all_lines:
                    raise CoreError('VALIDATION_ERROR', 'Строка уточнения не найдена в этой заявке.')
                if group == 'approvals' and patch is None:
                    result[group].pop(line_id, None)
                    continue
                if not isinstance(patch, dict):
                    raise CoreError('VALIDATION_ERROR', 'Уточнение строки должно быть объектом.')
                if group == 'line_updates':
                    if set(patch) - {'name', 'quantity', 'unit', 'requirements', 'client_unit_price', 'currency'}:
                        raise CoreError('VALIDATION_ERROR', 'Неизвестное поле позиции.')
                    for key in ('name', 'unit', 'currency'):
                        if key in patch and patch[key] is not None and not isinstance(patch[key], str):
                            raise CoreError('VALIDATION_ERROR', 'Некорректное текстовое поле позиции.')
                    for key in ('quantity', 'client_unit_price'):
                        if key in patch and patch[key] is not None:
                            try:
                                number = Decimal(patch[key]) if isinstance(patch[key], str) and len(patch[key]) <= 100 else None
                                if number is None or not number.is_finite() or number <= 0 or abs(number.as_tuple().exponent) > 30 or number.adjusted() > 30:
                                    raise ValueError
                            except (InvalidOperation, ValueError):
                                raise CoreError('VALIDATION_ERROR', 'Количество и цена должны быть положительными числами.') from None
                    if 'requirements' in patch and (not isinstance(patch['requirements'], list) or any(
                            not isinstance(item, dict) or set(item) != {'name', 'value'} or not all(isinstance(v, str) for v in item.values())
                            for item in patch['requirements'])):
                        raise CoreError('VALIDATION_ERROR', 'Некорректные характеристики товара.')
                    if set(patch) & {'name', 'unit', 'requirements'}:
                        result.setdefault('approvals', {}).pop(line_id, None)
                else:
                    if set(patch) - {'sku', 'analog_approved', 'adaptation_approved', 'cost_approved'} or not isinstance(patch.get('sku'), str) or not patch['sku']:
                        raise CoreError('VALIDATION_ERROR', 'Подтверждение должно указывать складской SKU.')
                    if any(type(value) is not bool for key, value in patch.items() if key != 'sku'):
                        raise CoreError('VALIDATION_ERROR', 'Подтверждения должны быть логическими значениями.')
                    if result[group].get(line_id, {}).get('sku') != patch['sku']:
                        result[group].pop(line_id, None)
                result[group][line_id] = {**result[group].get(line_id, {}), **patch}
        result.setdefault('_audit', []).append({'actor_id': actor.actor_id,
            'at': datetime.now(timezone.utc).isoformat(), 'changes': deepcopy(changes)})
        return result

    def create_revision(self, actor, request_id, base_revision_id, kind, decisions, command_key):
        for permission in ('process:start', 'artifact:read', 'artifact:write', 'settings:read'):
            authorize(actor, permission)
        validate_key(command_key)
        request_id, base_revision_id = _uuid(request_id), _uuid(base_revision_id)
        if kind not in ('manager_update', 'reanalyze'):
            raise CoreError('VALIDATION_ERROR', 'Неизвестный вид новой редакции.')
        intent = {'request_id': str(request_id), 'base_revision_id': str(base_revision_id), 'kind': kind, 'decisions': decisions}
        previous = self.repository.replay(actor, 'presales.revision.create', command_key, intent)
        if previous is not None:
            return previous
        base = self.repository.read_revision(actor.data_scope, request_id, base_revision_id)
        effective = self._merge_decisions(base['decisions'], decisions, base['extracted'] or {}, actor)
        inputs = deepcopy(base['inputs']) if kind == 'manager_update' else self._freeze(actor, base['inputs']['sources'])
        inputs['evaluated_at'] = datetime.now(timezone.utc).isoformat()
        if kind == 'reanalyze' and inputs['company']['id'] != base['inputs']['company']['id']:
            effective['approvals'] = {}
        if base['extracted'] is not None:
            inputs['cached_extracted'] = base['extracted']
        return self.repository.create_revision(actor, request_id, base_revision_id, kind, effective, inputs,
                                               command_key, intent=intent)

    def stop_job(self, actor, request_id, job_id, command_key):
        authorize(actor, 'process:start')
        validate_key(command_key)
        return self.repository.stop_job(actor, _uuid(request_id), _uuid(job_id), command_key)

    def resume_job(self, actor, request_id, job_id, command_key):
        authorize(actor, 'process:recover')
        validate_key(command_key)
        return self.repository.resume_job(actor, _uuid(request_id), _uuid(job_id), command_key)

    def run_once(self):
        from bervel.modules.presales.workflow import run_once
        return run_once(self)
