from uuid import UUID

from .errors import CoreError
from .models import (ActorContext, ArtifactRevision, ArtifactRevisionRef, RegisterArtifact,
                     ExtractArtifact, DatasetRevisionRef)
from .ports import ArtifactRepository, DatasetRepository, ObjectStore
from .registry import ModuleRegistry


def authorize(actor: ActorContext, permission: str) -> None:
    if permission not in actor.permissions:
        raise CoreError('FORBIDDEN', 'Operation is not permitted')


def validate_key(key: str) -> None:
    if not 1 <= len(key) <= 128 or not key.strip():
        raise CoreError('VALIDATION_ERROR', 'Command key must contain 1..128 characters')


class ArtifactService:
    def __init__(self, repository: ArtifactRepository, objects: ObjectStore):
        self.repository = repository
        self.objects = objects

    def register(self, context: ActorContext, command: RegisterArtifact) -> ArtifactRevisionRef:
        authorize(context, 'artifact:write')
        validate_key(command.command_key)
        if command.expected_revision < 0 or (command.artifact_id is None and command.expected_revision != 0):
            raise CoreError('VALIDATION_ERROR', 'Invalid expected revision')
        if len(command.content) > 5 * 1024 * 1024:
            raise CoreError('QUOTA_EXCEEDED', 'Original exceeds 5 MiB')
        if not command.content or not 1 <= len(command.filename) <= 255:
            raise CoreError('VALIDATION_ERROR', 'Nonempty original and filename are required')
        if command.source_role != 'test_fixture':
            raise CoreError('VALIDATION_ERROR', 'Only educational test_fixture sources are supported')
        if set(command.source) - {'origin_system', 'external_id', 'external_revision', 'received_at'} or any(
            v is not None and (not isinstance(v, str) or len(v) > 2000) for v in command.source.values()
        ):
            raise CoreError('VALIDATION_ERROR', 'Invalid source metadata')
        if not 1 <= len(command.media_type) <= 100:
            raise CoreError('VALIDATION_ERROR', 'Invalid media type')
        stored = self.objects.put(command.content)
        return self.repository.register_artifact(context, command, stored)

    def read(self, context: ActorContext, ref: ArtifactRevisionRef) -> ArtifactRevision:
        authorize(context, 'artifact:read')
        metadata = self.repository.read_artifact(context, ref)
        content = self.objects.read(metadata['object_ref'])
        import hashlib
        if hashlib.sha256(content).hexdigest() != metadata['sha256'] or len(content) != metadata['size']:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Stored original failed integrity verification')
        return ArtifactRevision(ref, metadata, content)

    def metadata(self, context: ActorContext, ref: ArtifactRevisionRef) -> dict:
        authorize(context, 'artifact:read')
        return self.repository.read_artifact(context, ref)

    def history(self, context: ActorContext, artifact_id: UUID) -> dict:
        authorize(context, 'artifact:read')
        authorize(context, 'dataset:read')
        return self.repository.artifact_history(context, artifact_id)


class ExtractionService:
    def __init__(self, artifacts: ArtifactService, repository: DatasetRepository, registry: ModuleRegistry):
        self.artifacts = artifacts
        self.repository = repository
        self.registry = registry

    def extract(self, context: ActorContext, command: ExtractArtifact) -> DatasetRevisionRef:
        authorize(context, 'dataset:publish')
        validate_key(command.command_key)
        if command.expected_revision < 0 or (command.dataset_id is None and command.expected_revision != 0):
            raise CoreError('VALIDATION_ERROR', 'Invalid expected dataset revision')
        original = self.artifacts.read(context, command.artifact_ref)
        if original.metadata['media_type'] != 'text/csv':
            raise CoreError('VALIDATION_ERROR', 'Educational extraction supports text/csv only; original is preserved')
        module = self.registry.get('demo.document-review', command.module_version)
        result = module.parser(original.content, command.artifact_ref)
        if not any(d['schema_id'] == result.schema_id and d['schema_version'] == result.schema_version for d in module.manifest['data_types']):
            raise CoreError('UNSUPPORTED_SCHEMA', 'Module produced an undeclared schema')
        self.registry.validate_records(result.schema_id, result.schema_version, result.records)
        if not result.records or len({r.record_id for r in result.records}) != len(result.records):
            raise CoreError('VALIDATION_ERROR', 'Result requires unique records')
        producer = {'module_id': module.manifest['module_id'], 'module_version': command.module_version,
                    'workflow_type': 'demo.document-review.parse', 'workflow_version': '1.0.0'}
        return self.repository.publish_extraction(context, command, result, producer)

    def read(self, context: ActorContext, ref: DatasetRevisionRef) -> dict:
        authorize(context, 'dataset:read')
        authorize(context, 'artifact:read')
        return self.repository.read_dataset(context, ref)

    def records(self, context: ActorContext, ref: DatasetRevisionRef, cursor: int = 0, limit: int = 100) -> dict:
        authorize(context, 'dataset:read')
        authorize(context, 'artifact:read')
        if cursor < 0 or not 1 <= limit <= 100:
            raise CoreError('VALIDATION_ERROR', 'Cursor must be nonnegative and limit must be 1..100')
        return self.repository.read_records(context, ref, cursor, limit)
