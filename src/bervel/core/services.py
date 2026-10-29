from uuid import UUID

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, Artifact, ArtifactRef, RegisterArtifact
from bervel.core.ports import ArtifactRepository, ObjectStore


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

    def register(self, context: ActorContext, command: RegisterArtifact) -> ArtifactRef:
        authorize(context, 'artifact:write')
        validate_key(command.command_key)
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
        with self.repository.original_mutation():
            return self.repository.register_artifact(context, command, self.objects.put)

    def delete(self, context: ActorContext, artifact_id: UUID) -> None:
        authorize(context, 'artifact:write')
        # Upload publication and object removal share a database lock across API processes.
        with self.repository.original_mutation():
            pending = self.repository.delete_artifact(context, artifact_id)
            for object_ref in pending:
                if not self.repository.object_is_referenced(object_ref):
                    try:
                        self.objects.delete(object_ref)
                    except CoreError as error:
                        raise CoreError('ARTIFACT_CLEANUP_PENDING',
                            'Файл убран из каталога, но очистка оригинала не завершена. Повторите удаление.') from error
                self.repository.finish_object_deletion(artifact_id, object_ref)

    def cleanup_deleted(self) -> bool:
        pending = self.repository.pending_deletions()
        failure = None
        for artifact_id, scope in pending:
            actor = ActorContext('original-cleanup', scope, frozenset({'artifact:write'}))
            try:
                self.delete(actor, artifact_id)
            except CoreError as error:
                failure = error
        if failure is not None:
            raise failure
        return bool(pending)

    def read(self, context: ActorContext, ref: ArtifactRef) -> Artifact:
        authorize(context, 'artifact:read')
        metadata = self.repository.read_artifact(context, ref)
        try:
            content = self.objects.read(metadata['object_ref'])
        except CoreError:
            self.repository.read_artifact(context, ref)  # Prefer Gone if deletion won the race with this read.
            raise
        import hashlib
        if hashlib.sha256(content).hexdigest() != metadata['sha256'] or len(content) != metadata['size']:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Stored original failed integrity verification')
        return Artifact(ref, metadata, content)

    def metadata(self, context: ActorContext, ref: ArtifactRef) -> dict:
        authorize(context, 'artifact:read')
        return self.repository.read_artifact(context, ref)

    def prepared_metadata(self, context: ActorContext, ref: ArtifactRef) -> dict:
        metadata = self.metadata(context, ref)
        if metadata['processing_state'] != 'ready' or metadata['completed_chunks'] != metadata['total_chunks']:
            raise CoreError('FILE_NOT_READY', 'Подготовка файла для ИИ ещё не завершена.')
        return metadata

    def set_usage_role(self, context: ActorContext, artifact_id: UUID, usage_role: str) -> dict:
        authorize(context, 'artifact:write')
        if usage_role not in ('general', 'procurement', 'stock', 'economics'):
            raise CoreError('VALIDATION_ERROR', 'Неизвестное назначение файла.')
        return self.repository.set_usage_role(context, artifact_id, usage_role)

    def list(self, context: ActorContext, cursor: int = 0, limit: int = 20) -> dict:
        authorize(context, 'artifact:read')
        authorize(context, 'dataset:read')
        if cursor < 0 or not 1 <= limit <= 100:
            raise CoreError('VALIDATION_ERROR', 'Cursor must be nonnegative and limit must be 1..100')
        return self.repository.list_artifacts(context, cursor, limit)
