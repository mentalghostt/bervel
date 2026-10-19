from datetime import datetime, timezone
from dataclasses import asdict
import hashlib
from uuid import UUID, uuid4

from sqlalchemy import select, insert, update, text, and_
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from bervel.core.errors import CoreError
from bervel.core.models import (ActorContext, ArtifactRevisionRef, RegisterArtifact, StoredObject,
                               DatasetRevisionRef, ExtractArtifact, ExtractionResult)
from bervel.core.validation import canonical_json
from .database import artifacts, artifact_revisions, schema_definitions, command_receipts, audit
from .database import datasets, dataset_revisions, data_records, process_runs, input_snapshots


def digest(value: dict) -> str:
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def lock(connection: Connection, key: str) -> None:
    lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True)
    connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock_id})


class PostgresRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def register_schemas(self, definitions: dict) -> None:
        try:
            with self.engine.begin() as connection:
                lock(connection, 'core:schema-registration')
                for (schema_id, version), schema in definitions.items():
                    checksum = digest(schema)
                    old = connection.execute(select(schema_definitions).where(
                        schema_definitions.c.schema_id == schema_id, schema_definitions.c.schema_version == version)).mappings().first()
                    if old and old['checksum'] != checksum:
                        raise CoreError('UNSUPPORTED_SCHEMA', 'Stored schema version is immutable')
                    if not old:
                        connection.execute(insert(schema_definitions).values(schema_id=schema_id,
                            schema_version=version, checksum=checksum, definition=schema))
        except SQLAlchemyError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Schema storage is unavailable') from error

    def _receipt_filter(self, actor: ActorContext, operation: str, key: str):
        return and_(command_receipts.c.data_scope == actor.data_scope, command_receipts.c.actor_id == actor.actor_id,
                    command_receipts.c.operation == operation, command_receipts.c.command_key == key)

    def _repeat(self, connection: Connection, actor: ActorContext, operation: str, key: str, fingerprint: str) -> dict | None:
        lock(connection, canonical_json([actor.data_scope, actor.actor_id, operation, key]))
        row = connection.execute(select(command_receipts).where(self._receipt_filter(actor, operation, key))).mappings().first()
        if row:
            if row['fingerprint'] != fingerprint:
                raise CoreError('IDEMPOTENCY_CONFLICT', 'Command key already used with different input')
            return row['result']
        return None

    def _finish(self, connection: Connection, actor: ActorContext, operation: str, key: str, fingerprint: str, result: dict, *, audited: bool = True) -> None:
        if audited:
            connection.execute(insert(audit).values(audit_id=uuid4(), data_scope=actor.data_scope, actor_id=actor.actor_id,
                operation=operation, object_ref=result, created_at=datetime.now(timezone.utc)))
        connection.execute(insert(command_receipts).values(data_scope=actor.data_scope, actor_id=actor.actor_id,
            operation=operation, command_key=key, fingerprint=fingerprint, result=result))

    def register_artifact(self, actor: ActorContext, command: RegisterArtifact, stored: StoredObject) -> ArtifactRevisionRef:
        significant = {'sha256': stored.sha256, 'size': stored.size, 'filename': command.filename,
                       'media_type': command.media_type, 'source': command.source, 'source_role': command.source_role}
        fingerprint = digest({**significant, 'artifact_id': str(command.artifact_id) if command.artifact_id else None,
                              'expected_revision': command.expected_revision})
        try:
            with self.engine.begin() as connection:
                repeat = self._repeat(connection, actor, 'artifact.register', command.command_key, fingerprint)
                if repeat:
                    ref = ArtifactRevisionRef(UUID(repeat['artifact_id']), repeat['revision'])
                    self._artifact(connection, actor, ref)
                    return ref
                artifact_id = command.artifact_id or uuid4()
                if command.artifact_id:
                    current = connection.execute(select(artifacts).where(artifacts.c.artifact_id == artifact_id,
                        artifacts.c.data_scope == actor.data_scope).with_for_update()).mappings().first()
                    if current is None:
                        raise CoreError('NOT_FOUND', 'Material not found')
                    if current['current_revision'] != command.expected_revision:
                        raise CoreError('STALE_REVISION', 'Material revision has changed')
                    old = self._artifact(connection, actor, ArtifactRevisionRef(artifact_id, command.expected_revision))
                    if all(old[k] == v for k, v in significant.items()):
                        ref = {'artifact_id': str(artifact_id), 'revision': command.expected_revision}
                        self._finish(connection, actor, 'artifact.register', command.command_key, fingerprint, ref, audited=False)
                        return ArtifactRevisionRef(artifact_id, command.expected_revision)
                    revision = command.expected_revision + 1
                    connection.execute(update(artifacts).where(artifacts.c.artifact_id == artifact_id).values(current_revision=revision))
                else:
                    revision = 1
                    connection.execute(insert(artifacts).values(artifact_id=artifact_id, data_scope=actor.data_scope,
                        owner_module_id='demo.document-review', current_revision=revision))
                ref = {'artifact_id': str(artifact_id), 'revision': revision}
                stored_metadata = {**significant, **ref, 'object_ref': stored.object_ref, 'data_scope': actor.data_scope,
                    'owner_module_id': 'demo.document-review', 'created_at': datetime.now(timezone.utc).isoformat()}
                connection.execute(insert(artifact_revisions).values(artifact_id=artifact_id, revision=revision, metadata=stored_metadata))
                self._finish(connection, actor, 'artifact.register', command.command_key, fingerprint, ref)
                return ArtifactRevisionRef(artifact_id, revision)
        except SQLAlchemyError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Material storage is unavailable') from error

    def _artifact(self, connection: Connection, actor: ActorContext, ref: ArtifactRevisionRef) -> dict:
        row = connection.execute(select(artifact_revisions.c.metadata).join(artifacts).where(
            artifacts.c.data_scope == actor.data_scope, artifact_revisions.c.artifact_id == ref.artifact_id,
            artifact_revisions.c.revision == ref.revision)).scalar_one_or_none()
        if row is None:
            raise CoreError('NOT_FOUND', 'Material not found')
        return row

    def read_artifact(self, actor: ActorContext, ref: ArtifactRevisionRef) -> dict:
        try:
            with self.engine.connect() as connection:
                return self._artifact(connection, actor, ref)
        except SQLAlchemyError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Material storage is unavailable') from error

    def artifact_history(self, actor: ActorContext, artifact_id: UUID) -> dict:
        try:
            with self.engine.connect() as connection:
                if not connection.execute(select(artifacts.c.artifact_id).where(
                    artifacts.c.artifact_id == artifact_id, artifacts.c.data_scope == actor.data_scope)).first():
                    raise CoreError('NOT_FOUND', 'Material not found')
                revisions = list(connection.execute(select(artifact_revisions.c.metadata).where(
                    artifact_revisions.c.artifact_id == artifact_id).order_by(artifact_revisions.c.revision)).scalars())
                results = list(connection.execute(select(dataset_revisions.c.envelope).join(datasets).where(
                    dataset_revisions.c.artifact_id == artifact_id, datasets.c.data_scope == actor.data_scope
                ).order_by(dataset_revisions.c.dataset_id, dataset_revisions.c.revision)).scalars())
                return {'artifact_id': str(artifact_id), 'revisions': revisions, 'datasets': results}
        except SQLAlchemyError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'History storage is unavailable') from error

    def publish_extraction(self, actor: ActorContext, command: ExtractArtifact, result: ExtractionResult,
                           producer: dict[str, str]) -> DatasetRevisionRef:
        try:
            with self.engine.begin() as connection:
                return self._publish_extraction(connection, actor, command, result, producer)
        except SQLAlchemyError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Dataset publication failed; no partial result was published') from error

    def _publish_extraction(self, connection: Connection, actor: ActorContext, command: ExtractArtifact,
                            result: ExtractionResult, producer: dict[str, str],
                            existing_run: tuple[UUID, UUID] | None = None) -> DatasetRevisionRef:
        fingerprint = digest({'artifact_id': str(command.artifact_ref.artifact_id), 'artifact_revision': command.artifact_ref.revision,
            'module_id': producer['module_id'], 'module_version': command.module_version,
            'dataset_id': str(command.dataset_id) if command.dataset_id else None, 'expected_revision': command.expected_revision})
        self._artifact(connection, actor, command.artifact_ref)
        operation = 'process.dataset.extract' if existing_run else 'dataset.extract'
        repeat = self._repeat(connection, actor, operation, command.command_key, fingerprint)
        if repeat:
            ref = DatasetRevisionRef(UUID(repeat['dataset_id']), repeat['revision'])
            self._dataset(connection, actor, ref)
            return ref
        dataset_id = command.dataset_id or uuid4()
        if command.dataset_id:
            current = connection.execute(select(datasets).where(datasets.c.dataset_id == dataset_id,
                datasets.c.data_scope == actor.data_scope).with_for_update()).mappings().first()
            if current is None:
                raise CoreError('NOT_FOUND', 'Dataset not found')
            if current['artifact_id'] != command.artifact_ref.artifact_id:
                raise CoreError('VALIDATION_ERROR', 'Dataset revisions must belong to the same material')
            if current['current_revision'] != command.expected_revision:
                raise CoreError('STALE_REVISION', 'Dataset revision has changed')
            revision = command.expected_revision + 1
            connection.execute(update(datasets).where(datasets.c.dataset_id == dataset_id).values(current_revision=revision))
        else:
            revision = 1
            connection.execute(insert(datasets).values(dataset_id=dataset_id, artifact_id=command.artifact_ref.artifact_id,
                data_scope=actor.data_scope, owner_module_id=producer['module_id'], current_revision=revision))
        now = datetime.now(timezone.utc)
        if existing_run:
            run_id, snapshot_id = existing_run
        else:
            snapshot_id, run_id = uuid4(), uuid4()
            inputs = [{'artifact_id': str(command.artifact_ref.artifact_id), 'revision': command.artifact_ref.revision,
                       'role': 'test_fixture'}]
            connection.execute(insert(input_snapshots).values(input_snapshot_id=snapshot_id, data_scope=actor.data_scope, inputs=inputs))
            connection.execute(insert(process_runs).values(process_run_id=run_id, data_scope=actor.data_scope,
                input_snapshot_id=snapshot_id, producer=producer, configuration_revision=1, state='COMPLETED', created_at=now))
        envelope = {'envelope_version': '0.1.0', 'dataset_id': str(dataset_id), 'revision': revision,
            'data_type': result.schema_id, 'schema_version': result.schema_version, 'owner_module_id': producer['module_id'],
            'data_scope': actor.data_scope, 'producer': producer, 'process_run_id': str(run_id),
            'input_snapshot_id': str(snapshot_id), 'configuration_revision': 1, 'created_at': now.isoformat(),
            'observed_at': None, 'quality_state': result.quality_state, 'record_count': len(result.records)}
        connection.execute(insert(dataset_revisions).values(dataset_id=dataset_id, revision=revision,
            artifact_id=command.artifact_ref.artifact_id, artifact_revision=command.artifact_ref.revision,
            input_snapshot_id=snapshot_id, process_run_id=run_id, schema_id=result.schema_id,
            schema_version=result.schema_version, envelope=envelope, record_count=len(result.records)))
        for ordinal, record in enumerate(result.records):
            connection.execute(insert(data_records).values(dataset_id=dataset_id, revision=revision,
                ordinal=ordinal, record_id=record.record_id, record=asdict(record)))
        response = {'dataset_id': str(dataset_id), 'revision': revision}
        self._finish(connection, actor, operation, command.command_key, fingerprint, response)
        return DatasetRevisionRef(dataset_id, revision)

    def _dataset(self, connection: Connection, actor: ActorContext, ref: DatasetRevisionRef) -> dict:
        source = dataset_revisions.join(datasets, dataset_revisions.c.dataset_id == datasets.c.dataset_id).join(
            artifacts, datasets.c.artifact_id == artifacts.c.artifact_id)
        row = connection.execute(select(dataset_revisions).select_from(source).where(
            datasets.c.data_scope == actor.data_scope, artifacts.c.data_scope == actor.data_scope,
            dataset_revisions.c.dataset_id == ref.dataset_id, dataset_revisions.c.revision == ref.revision)).mappings().first()
        if row is None:
            raise CoreError('NOT_FOUND', 'Dataset not found')
        return dict(row)

    def read_dataset(self, actor: ActorContext, ref: DatasetRevisionRef) -> dict:
        try:
            with self.engine.connect() as connection:
                row = self._dataset(connection, actor, ref)
                snapshot = connection.execute(select(input_snapshots).where(
                    input_snapshots.c.input_snapshot_id == row['input_snapshot_id'])).mappings().one()
                return {**row['envelope'], 'input_snapshot': {'input_snapshot_id': str(snapshot['input_snapshot_id']),
                    'data_scope': snapshot['data_scope'], 'inputs': snapshot['inputs']}}
        except SQLAlchemyError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Dataset storage is unavailable') from error

    def read_records(self, actor: ActorContext, ref: DatasetRevisionRef, cursor: int, limit: int) -> dict:
        try:
            with self.engine.connect() as connection:
                row = self._dataset(connection, actor, ref)
                records = list(connection.execute(select(data_records.c.record).where(data_records.c.dataset_id == ref.dataset_id,
                    data_records.c.revision == ref.revision, data_records.c.ordinal >= cursor).order_by(data_records.c.ordinal).limit(limit)).scalars())
                following = cursor + len(records)
                return {'dataset_id': str(ref.dataset_id), 'revision': ref.revision, 'records': records,
                        'next_cursor': following if following < row['record_count'] else None}
        except SQLAlchemyError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Dataset storage is unavailable') from error
