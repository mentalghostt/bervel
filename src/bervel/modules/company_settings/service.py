"""Settings use cases. External dependencies are supplied by the composition root."""
from collections.abc import Callable
from copy import deepcopy
import hashlib
from pathlib import PurePath
from typing import Protocol
from uuid import UUID, uuid5, NAMESPACE_URL

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, Artifact, ArtifactRef
from bervel.core.services import authorize, validate_key
from bervel.modules.company_settings.models import Profile, Snapshot, validate_profile, stock_for_profile


class SettingsRepository(Protocol):
    def read(self, data_scope: str) -> Snapshot | None: ...
    def read_snapshot(self, data_scope: str, snapshot_id: UUID) -> Snapshot | None: ...
    def replay(self, actor: ActorContext, base_snapshot_id: UUID | None, profile: Profile, command_key: str) -> Snapshot | None: ...
    def save(self, actor: ActorContext, base_snapshot_id: UUID | None, profile: Profile, command_key: str) -> Snapshot: ...


class StockArtifacts(Protocol):
    def read(self, context: ActorContext, ref: ArtifactRef) -> Artifact: ...


class CompanySettings:
    def __init__(self, repository: SettingsRepository, artifacts: StockArtifacts, parse_stock: Callable[[bytes], dict]):
        self.repository = repository
        self.artifacts = artifacts
        self.parse_stock = parse_stock

    def read(self, actor: ActorContext) -> Snapshot | None:
        authorize(actor, 'settings:read')
        return self.repository.read(actor.data_scope)

    def read_analysis_snapshot(self, actor: ActorContext) -> dict:
        """Capture company-owned facts through its public service, without writing a head."""
        from datetime import datetime, timezone
        snapshot = self.read(actor)
        if snapshot is None:
            raise CoreError('SETTINGS_REQUIRED', 'Сначала сохраните общие настройки предприятия и выберите склад.')
        profile = deepcopy(snapshot['profile'])
        artifact, stock = self._stock(actor, UUID(profile['stock_artifact_id']))
        if hashlib.sha256(artifact.content).hexdigest() != profile['stock_sha256']:
            raise CoreError('VALIDATION_ERROR', 'Выбранный склад не совпадает со снимком предприятия.')
        return {'id': str(snapshot['id']), 'profile': profile, 'stock': stock_for_profile(stock, profile),
                'evaluated_at': datetime.now(timezone.utc).isoformat()}

    def read_snapshot(self, data_scope: str, snapshot_id: UUID) -> Snapshot:
        snapshot = self.repository.read_snapshot(data_scope, snapshot_id)
        if snapshot is None:
            raise CoreError('NOT_FOUND', 'Снимок настроек не найден в этой области доступа.')
        return snapshot

    def _stock(self, actor: ActorContext, artifact_id: UUID) -> tuple[Artifact, dict]:
        authorize(actor, 'artifact:read')
        artifact = self.artifacts.read(actor, ArtifactRef(artifact_id))
        metadata = artifact.metadata
        if PurePath(metadata['filename']).suffix.casefold() != '.csv':
            raise CoreError('VALIDATION_ERROR', 'Выберите складской CSV.')
        return artifact, self.parse_stock(artifact.content)

    def preview(self, actor: ActorContext, stock_artifact_id: UUID, base_snapshot_id: UUID | None = None) -> dict:
        authorize(actor, 'settings:read')
        artifact, stock = self._stock(actor, stock_artifact_id)
        warnings = []
        imported = []
        for row in stock['rows']:
            imported.append(dict(id=str(uuid5(NAMESPACE_URL, 'bervel:company-product:'+row['sku'])),
                sku=row['sku'],name=row['product'],unit=row['unit'],attributes=deepcopy(row['attributes']),
                enabled=True))
        profile = dict(stock_artifact_id=str(stock_artifact_id),stock_sha256=hashlib.sha256(artifact.content).hexdigest(),
            products=imported,experimental_products=[],include_experimental=False,production=[],resources=[],scoring_policy={})
        if base_snapshot_id is not None:
            base = self.read_snapshot(actor.data_scope, base_snapshot_id)['profile']
            profile.update({key:deepcopy(base[key]) for key in ('experimental_products','include_experimental','production','resources','scoring_policy')})
            by_sku = {p['sku']:p for p in base['products'] if p['sku'] is not None}
            used = set()
            merged = []
            for row in imported:
                old = by_sku.get(row['sku'])
                if old is not None:
                    used.add(old['id'])
                    merged.append(deepcopy(old))
                    if any(old[key] != row[key] for key in ('name','unit','attributes')):
                        warnings.append(f'SKU {row["sku"]}: сохранены ручные поля базового снимка; импорт отличается. Проверьте наименование, единицу и атрибуты.')
                else:
                    merged.append(row)
            missing = [deepcopy(p) for p in base['products'] if p['id'] not in used]
            for row in missing:
                warnings.append(f'Продукт {row["name"]}: отсутствует в новом складе; сохранён в каталоге с нулевым остатком.')
            profile['products'] = merged + missing
            warnings.append('Производство, ресурсы, политика и переключатели сохранены из базового снимка; импорт не меняет подтверждённые факты.')
        elif self.repository.read(actor.data_scope) is not None:
            warnings.append('Предпросмотр создан без базового снимка. Для сохранения ручных настроек повторите импорт с base_snapshot_id текущего снимка.')
        if any(set(p['attributes']) & {'manufactured_before','units_per_workday','production_unit_cost','resource_group_id'} for p in imported):
            warnings.append('Производственные колонки сохранены в атрибутах источника. Подтвердите мощности и затраты в редакторе перед сохранением.')
        return {'profile':validate_profile(profile), 'warnings':warnings}

    def save(self, actor: ActorContext, base_snapshot_id: UUID | None, profile: Profile, command_key: str) -> Snapshot:
        authorize(actor, 'settings:write')
        authorize(actor, 'artifact:read')
        validate_key(command_key)
        checked = validate_profile(profile)
        # A replay remains readable even if the source was later removed.
        existing = self.repository.replay(actor, base_snapshot_id, checked, command_key)
        if existing is not None:
            return existing
        artifact, stock = self._stock(actor, UUID(checked['stock_artifact_id']))
        if hashlib.sha256(artifact.content).hexdigest() != checked['stock_sha256']:
            raise CoreError('VALIDATION_ERROR', 'Отпечаток профиля отличается от выбранного складского файла.')
        units = {r['sku']:r['unit'] for r in stock['rows']}
        for index, product in enumerate(checked['products']):
            if product['sku'] in units and units[product['sku']] != product['unit']:
                raise CoreError('VALIDATION_ERROR', f'products[{index}].unit: единица отличается от складской; требуется исправить источник или продукт.')
        return self.repository.save(actor, base_snapshot_id, checked, command_key)
