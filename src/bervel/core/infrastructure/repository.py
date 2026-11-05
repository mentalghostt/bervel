from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy import select, insert, update, and_, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, ArtifactRef, RegisterArtifact, StoredObject
from bervel.core.infrastructure.database import artifacts


from bervel.core.infrastructure.commands import CommandReceipts, digest
from bervel.core.infrastructure.knowledge import queue_artifact, knowledge_lock_key
from bervel.core.originals import OriginalContent, content_digest


def processing_fields(row) -> dict:
    return {'processing_state': row['state'] if row else 'pending',
            'completed_chunks': row['completed_chunks'] if row else 0,
            'total_chunks': row['total_chunks'] if row else 0,
            'processing_error': row['error'] if row else None}


class PostgresRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.receipts = CommandReceipts()


    @contextmanager
    def original_mutation(self):
        try:
            with self.engine.begin() as connection:
                # Separate two-integer advisory namespace; held through object put/unlink and SQL commit.
                connection.execute(text('SELECT pg_advisory_xact_lock(1380275028, 1)'))
                yield
        except SQLAlchemyError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Material storage is unavailable') from error

    def register_artifact(self, actor: ActorContext, command: RegisterArtifact, store: Callable[[OriginalContent], StoredObject]) -> ArtifactRef:
        checksum, size = content_digest(command.content)
        significant = {'sha256': checksum, 'size': size, 'filename': command.filename,
                       'media_type': command.media_type, 'source': command.source, 'source_role': command.source_role}
        fingerprint = digest(significant)
        try:
            with self.engine.begin() as connection:
                repeat = self.receipts.repeat(connection, actor, 'artifact.register', command.command_key, fingerprint)
                if repeat:
                    ref = ArtifactRef(UUID(repeat['artifact_id']))
                    self._artifact(connection, actor, ref)
                    return ref
                artifact_id = uuid4()
                ref = {'artifact_id': str(artifact_id)}
                stored = store(command.content)
                if stored.sha256 != checksum or stored.size != size:
                    raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Original changed during registration; file was not registered')
                stored_metadata = {**significant, **ref, 'object_ref': stored.object_ref, 'data_scope': actor.data_scope,
                    'owner_module_id': 'demo.document-review', 'created_at': datetime.now(timezone.utc).isoformat()}
                connection.execute(insert(artifacts).values(artifact_id=artifact_id, data_scope=actor.data_scope, owner_module_id='demo.document-review', metadata=stored_metadata))
                queue_artifact(connection, ArtifactRef(artifact_id))
                self.receipts.finish(connection, actor, 'artifact.register', command.command_key, fingerprint, ref)
                return ArtifactRef(artifact_id)
        except SQLAlchemyError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Material storage is unavailable') from error

    def _artifact(self, connection: Connection, actor: ActorContext, ref: ArtifactRef) -> dict:
        self._active_artifact(connection, actor, ref.artifact_id)
        row = connection.execute(select(artifacts.c.metadata, artifacts.c.usage_role, artifacts.c.deleted_at).where(
            artifacts.c.data_scope == actor.data_scope, artifacts.c.artifact_id == ref.artifact_id)).mappings().one()
        if row['deleted_at'] is not None or row['metadata'] is None:
            raise CoreError('ARTIFACT_DELETED', 'Файл удалён.')
        job = connection.execute(text('SELECT state,error,total_chunks,completed_chunks FROM knowledge_jobs WHERE artifact_id=:id'),
                                 {'id':ref.artifact_id}).mappings().first()
        return {**row['metadata'], 'usage_role':row['usage_role'], **processing_fields(job)}

    def _active_artifact(self, connection: Connection, actor: ActorContext, artifact_id: UUID) -> None:
        row = connection.execute(select(artifacts.c.deleted_at).where(
            artifacts.c.artifact_id == artifact_id, artifacts.c.data_scope == actor.data_scope)).first()
        if row is None:
            raise CoreError('NOT_FOUND', 'Material not found')
        if row.deleted_at is not None:
            raise CoreError('ARTIFACT_DELETED', 'Файл удалён.')

    def read_artifact(self, actor: ActorContext, ref: ArtifactRef) -> dict:
        try:
            with self.engine.connect() as connection:
                return self._artifact(connection, actor, ref)
        except SQLAlchemyError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Material storage is unavailable') from error

    def list_artifacts(self, actor: ActorContext, cursor: int, limit: int) -> dict:
        statement = (select(artifacts.c.metadata, artifacts.c.usage_role)
            .where(artifacts.c.data_scope == actor.data_scope, artifacts.c.deleted_at.is_(None))
            .order_by(artifacts.c.metadata['created_at'].astext.desc(), artifacts.c.artifact_id.desc())
            .offset(cursor).limit(limit + 1))
        try:
            with self.engine.connect() as connection:
                rows = list(connection.execute(statement).mappings())
                ids = [UUID(row['metadata']['artifact_id']) for row in rows[:limit]]
                jobs = connection.execute(text('''SELECT j.artifact_id,j.state,j.error,j.total_chunks,j.completed_chunks
                    FROM knowledge_jobs j JOIN artifacts a ON a.artifact_id=j.artifact_id
                    WHERE a.data_scope=:scope AND a.artifact_id=ANY(:ids)'''),{'scope':actor.data_scope,'ids':ids}).mappings().all() if ids else []
                states = {str(job['artifact_id']):job for job in jobs}
            items = []
            for row in rows[:limit]:
                item = {key: row['metadata'][key] for key in
                        ('artifact_id', 'filename', 'media_type', 'size', 'created_at')}
                item.update(usage_role=row['usage_role'], **processing_fields(states.get(item['artifact_id'])))
                items.append(item)
            return {'artifacts': items, 'next_cursor': cursor + limit if len(rows) > limit else None}
        except SQLAlchemyError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Material list is unavailable') from error

    def set_usage_role(self, actor: ActorContext, artifact_id: UUID, usage_role: str) -> dict:
        try:
            with self.engine.begin() as connection:
                self._active_artifact(connection, actor, artifact_id)
                connection.execute(update(artifacts).where(artifacts.c.artifact_id == artifact_id,
                    artifacts.c.data_scope == actor.data_scope).values(usage_role=usage_role))
            return {'artifact_id':str(artifact_id),'usage_role':usage_role}
        except SQLAlchemyError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Material storage is unavailable') from error

    def delete_artifact(self, actor: ActorContext, artifact_id: UUID) -> list[str]:
        with self.engine.begin() as connection:
            current = connection.execute(select(artifacts).where(artifacts.c.artifact_id == artifact_id,
                artifacts.c.data_scope == actor.data_scope).with_for_update()).mappings().first()
            if current is None:
                raise CoreError('NOT_FOUND', 'Material not found')
            key = knowledge_lock_key(ArtifactRef(artifact_id))
            if not connection.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key':key}).scalar():
                raise CoreError('ARTIFACT_BUSY', 'Файл сейчас подготавливается для ИИ. Повторите удаление после завершения текущей порции.')
            params = {'id':artifact_id}
            for object_ref in ([current['metadata']['object_ref']] if current['metadata'] else []):
                connection.execute(text('INSERT INTO artifact_object_deletions(artifact_id,object_ref) VALUES(:id,:ref) ON CONFLICT DO NOTHING'),
                                   {**params, 'ref':object_ref})
            for table in ('knowledge_chunks', 'knowledge_jobs', 'document_indexes', 'csv_chunks'):
                connection.execute(text(f'DELETE FROM {table} WHERE artifact_id=:id'), params)
            # Historical dataset revisions may share a dataset: remove only this original's revisions.
            dataset_ids = list(connection.execute(text('SELECT DISTINCT dataset_id FROM dataset_revisions WHERE artifact_id=:id'), params).scalars())
            connection.execute(text('''DELETE FROM data_records r USING dataset_revisions d
                WHERE r.dataset_id=d.dataset_id AND r.revision=d.revision AND d.artifact_id=:id'''), params)
            connection.execute(text('DELETE FROM dataset_revisions WHERE artifact_id=:id'), params)
            if dataset_ids:
                dataset_params = {'ids':dataset_ids}
                connection.execute(text('''UPDATE datasets d SET (current_revision,artifact_id)=(
                    SELECT r.revision,r.artifact_id FROM dataset_revisions r
                    WHERE r.dataset_id=d.dataset_id ORDER BY r.revision DESC LIMIT 1)
                    WHERE d.dataset_id=ANY(:ids)
                    AND NOT EXISTS(SELECT 1 FROM dataset_revisions r WHERE r.dataset_id=d.dataset_id AND r.revision=d.current_revision)
                    AND EXISTS(SELECT 1 FROM dataset_revisions r WHERE r.dataset_id=d.dataset_id)'''), dataset_params)
                connection.execute(text('''DELETE FROM datasets d WHERE d.dataset_id=ANY(:ids)
                    AND NOT EXISTS(SELECT 1 FROM dataset_revisions r WHERE r.dataset_id=d.dataset_id)'''), dataset_params)
            # Only identity/scope and a deletion marker survive, so historical links can return Gone.
            connection.execute(update(artifacts).where(artifacts.c.artifact_id == artifact_id).values(
                deleted_at=current['deleted_at'] or datetime.now(timezone.utc), metadata=None))
            return list(connection.execute(text('SELECT object_ref FROM artifact_object_deletions WHERE artifact_id=:id ORDER BY object_ref'), params).scalars())

    def object_is_referenced(self, object_ref: str) -> bool:
        with self.engine.connect() as connection:
            return connection.execute(select(artifacts.c.artifact_id).where(
                artifacts.c.metadata['object_ref'].astext == object_ref).limit(1)).first() is not None

    def finish_object_deletion(self, artifact_id: UUID, object_ref: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(text('DELETE FROM artifact_object_deletions WHERE artifact_id=:id AND object_ref=:ref'),
                               {'id':artifact_id, 'ref':object_ref})

    def pending_deletions(self) -> list[tuple[UUID, str]]:
        with self.engine.connect() as connection:
            return [tuple(row) for row in connection.execute(text('''SELECT DISTINCT a.artifact_id,a.data_scope
                FROM artifacts a JOIN artifact_object_deletions d USING(artifact_id)
                WHERE a.deleted_at IS NOT NULL ORDER BY a.artifact_id'''))]
