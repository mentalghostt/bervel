from copy import deepcopy
import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as UUIDColumn, insert
from sqlalchemy.engine import Connection, Engine

from bervel.core.errors import CoreError
from bervel.core.infrastructure.database import metadata
from bervel.core.models import ActorContext
from bervel.modules.company_settings.models import Profile, Snapshot

snapshots = Table('company_settings_snapshots', metadata,
    Column('id',UUIDColumn(as_uuid=True),primary_key=True),
    Column('data_scope',String(128),nullable=False),
    Column('actor_id',String(128),nullable=False),
    Column('command_key',String(128),nullable=False),
    Column('fingerprint',String(64),nullable=False),
    Column('profile',JSONB,nullable=False),
    Column('created_at',DateTime(timezone=True),nullable=False,server_default=func.now()))
heads = Table('company_settings_heads', metadata,
    Column('data_scope',String(128),primary_key=True),
    Column('snapshot_id',UUIDColumn(as_uuid=True),ForeignKey('company_settings_snapshots.id'),nullable=False))
experimental = Table('company_settings_experimental_products',metadata,
    Column('snapshot_id',UUIDColumn(as_uuid=True),ForeignKey('company_settings_snapshots.id'),primary_key=True),
    Column('ordinal',Integer,primary_key=True),
    Column('product',JSONB,nullable=False))


def _fingerprint(base_snapshot_id: UUID | None, profile: Profile) -> str:
    payload = {'base_snapshot_id':str(base_snapshot_id) if base_snapshot_id is not None else None,'profile':profile}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


class PostgresCompanySettingsRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _view(conn: Connection, row) -> Snapshot | None:
        if row is None:
            return None
        profile = deepcopy(row['profile'])
        profile['experimental_products'] = list(conn.execute(select(experimental.c.product).where(
            experimental.c.snapshot_id == row['id']).order_by(experimental.c.ordinal)).scalars())
        return {'id':row['id'],'created_at':row['created_at'],'profile':profile}

    def read(self, data_scope: str) -> Snapshot | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(snapshots).join(heads,heads.c.snapshot_id == snapshots.c.id).where(
                heads.c.data_scope == data_scope,snapshots.c.data_scope == data_scope)).mappings().one_or_none()
            return self._view(conn,row)

    def read_snapshot(self, data_scope: str, snapshot_id: UUID) -> Snapshot | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(snapshots).where(snapshots.c.data_scope == data_scope,
                snapshots.c.id == snapshot_id)).mappings().one_or_none()
            return self._view(conn,row)

    def _replay(self, conn: Connection, actor: ActorContext, key: str, fingerprint: str) -> Snapshot | None:
        row = conn.execute(select(snapshots).where(snapshots.c.data_scope == actor.data_scope,
            snapshots.c.actor_id == actor.actor_id,snapshots.c.command_key == key)).mappings().one_or_none()
        if row is not None and row['fingerprint'] != fingerprint:
            raise CoreError('IDEMPOTENCY_CONFLICT','Ключ сохранения уже использован с другим профилем или базовым снимком.')
        return self._view(conn,row)

    def replay(self, actor: ActorContext, base_snapshot_id: UUID | None, profile: Profile, command_key: str) -> Snapshot | None:
        with self.engine.connect() as conn:
            return self._replay(conn,actor,command_key,_fingerprint(base_snapshot_id,profile))

    def save(self, actor: ActorContext, base_snapshot_id: UUID | None, profile: Profile, command_key: str) -> Snapshot:
        fingerprint = _fingerprint(base_snapshot_id,profile)
        with self.engine.begin() as conn:
            # Serializes even the first write when the scope has no head row yet.
            conn.execute(select(func.pg_advisory_xact_lock(func.hashtextextended('company-settings:'+actor.data_scope,0))))
            previous = self._replay(conn,actor,command_key,fingerprint)
            if previous is not None:
                return previous
            current = conn.execute(select(heads.c.snapshot_id).where(heads.c.data_scope == actor.data_scope)).scalar_one_or_none()
            if current != base_snapshot_id:
                raise CoreError('SETTINGS_CONFLICT','Настройки уже изменены. Загрузите текущий снимок и повторите правки.')
            stored = deepcopy(profile)
            experimentals = stored.pop('experimental_products')
            row = conn.execute(snapshots.insert().values(id=uuid4(),data_scope=actor.data_scope,actor_id=actor.actor_id,
                command_key=command_key,fingerprint=fingerprint,profile=stored).returning(snapshots)).mappings().one()
            if experimentals:
                conn.execute(experimental.insert(),[dict(snapshot_id=row['id'],ordinal=i,product=p) for i,p in enumerate(experimentals)])
            conn.execute(insert(heads).values(data_scope=actor.data_scope,snapshot_id=row['id']).on_conflict_do_update(
                index_elements=[heads.c.data_scope],set_={'snapshot_id':row['id']}))
            return self._view(conn,row)
