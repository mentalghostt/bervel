"""Proposal ownership, immutable payloads and explicit browser decisions."""
from bervel.core.errors import CoreError
from bervel.core.services import authorize, validate_key


class EngineeringService:
    def __init__(self, repository, executor, api_reader, safety_filter=None):
        self.repository, self.executor, self.api_reader = repository, executor, api_reader
        self.safety_filter = safety_filter

    def _safe(self, value):
        clean = self.executor.redact(value)
        return self.safety_filter(clean) if self.safety_filter is not None else clean

    def perform(self, actor, session_id, turn_id, ordinal, operation, arguments):
        authorize(actor, 'chat:write')
        self.repository.check_session(actor, session_id, turn_id)
        if operation == 'code.propose':
            title, explanation = arguments.get('title'), arguments.get('explanation')
            if not isinstance(title, str) or not 1 <= len(title) <= 200 or not isinstance(explanation, str) or not 1 <= len(explanation) <= 4000:
                raise CoreError('VALIDATION_ERROR', 'Укажите заголовок и объяснение патча.')
            prepared = self.executor.prepare(arguments['changes'])
            if self._safe(prepared) != prepared:
                raise CoreError('FORBIDDEN', 'Предложение содержит известные секреты и не может быть сохранено.')
            metadata = self._safe({'title': title, 'explanation': explanation})
            return self.repository.create(actor, session_id, turn_id, ordinal, metadata, prepared)
        if operation == 'api.read': return self._safe(self.api_reader.read(actor, arguments['path']))
        if operation in {'diagnostic.run', 'test.run'}: return self._safe(self.executor.run(operation, arguments))
        raise CoreError('FORBIDDEN', 'Неизвестная инженерная операция.')

    def read(self, actor, proposal_id):
        authorize(actor, 'chat:read')
        return self._safe(self.repository.read(actor, proposal_id))

    def confirm(self, actor, proposal_id, command_key, fingerprint):
        authorize(actor, 'chat:write'); validate_key(command_key)
        with self.repository.claim(actor, proposal_id, command_key, fingerprint, 'confirm') as claim:
            if claim['execute']:
                try:
                    if self._safe(claim['payload']) != claim['payload']:
                        raise CoreError('FORBIDDEN', 'Предложение теперь содержит известный секрет. Применение запрещено.')
                    outcome = self.executor.apply(str(proposal_id), claim['payload'])
                except CoreError as error:
                    self.repository.finish(proposal_id, 'failed' if error.code in {'STALE_REVISION', 'FORBIDDEN'} else 'unknown')
                    raise
                except Exception:
                    self.repository.finish(proposal_id, 'unknown')
                    raise CoreError('ENGINE_RESPONSE_UNKNOWN', 'Исход применения неизвестен. Автоматический повтор запрещён.') from None
                self.repository.finish(proposal_id, outcome['status'])
        return self._safe(self.repository.read(actor, proposal_id))

    def reject(self, actor, proposal_id, command_key, fingerprint):
        authorize(actor, 'chat:write'); validate_key(command_key)
        with self.repository.claim(actor, proposal_id, command_key, fingerprint, 'reject') as claim:
            if claim['execute']: self.repository.finish(proposal_id, 'rejected')
        return self._safe(self.repository.read(actor, proposal_id))
