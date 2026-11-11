"""Inspection has no reference to file writers, settings, radar or Docker controls."""
from uuid import UUID
from bervel.core.errors import CoreError
from bervel.core.models import ActorContext
from bervel.core.services import authorize
from bervel.modules.inspection.contracts import validate_operation, ENGINEERING_OPERATIONS, AUTHORIZED_OPERATIONS


class InspectionActions:
    supports_session = True

    def __init__(self, *, reader, receipts, tickets, engineering=None, documents=None):
        self.reader, self.receipts, self.tickets = reader, receipts, tickets
        self.engineering, self.documents = engineering, documents

    def clean(self, value):
        return self.reader.redact(value)

    def execute(self, actor: ActorContext, turn_id: UUID, ordinal: int, operation: str, arguments: dict,
                *, ticket_authorized: bool = False, operation_authorized: bool = False,
                session_id: UUID | None = None, conversation=None) -> dict:
        authorize(actor, 'chat:write')
        validate_operation(operation, arguments)
        if operation == 'ticket.create' and ticket_authorized is not True:
            raise CoreError('ACTION_NOT_AUTHORIZED', 'Создание обращения требует прямой просьбы пользователя.')
        if operation in AUTHORIZED_OPERATIONS - {'ticket.create'} and operation_authorized is not True:
            raise CoreError('ACTION_NOT_AUTHORIZED', 'Операция требует прямой просьбы в текущем сообщении.')
        if operation in ENGINEERING_OPERATIONS and (self.engineering is None or session_id is None):
            raise CoreError('INSPECTION_FEATURE_UNAVAILABLE', 'Инженерные операции сейчас недоступны.')
        if operation == 'document.create' and self.documents is None:
            raise CoreError('INSPECTION_FEATURE_UNAVAILABLE', 'Создание документов сейчас недоступно.')
        if operation == 'conversation.read' and conversation is None:
            raise CoreError('INSPECTION_FEATURE_UNAVAILABLE', 'Контекст текущего диалога недоступен.')
        safe_arguments = self.clean(arguments)
        receipt = self.receipts.begin(actor, turn_id, ordinal, operation, safe_arguments)
        if receipt['state'] == 'completed': return self.clean(receipt['result'])
        if receipt['state'] == 'failed': raise CoreError(receipt['error']['code'], receipt['error']['message'])
        if receipt['state'] != 'new':
            raise CoreError('ACTION_OUTCOME_UNKNOWN', 'Исход действия неизвестен; автоматический повтор отключён.')
        identifier = UUID(receipt['action_id'])
        try:
            if operation == 'ticket.create':
                ticket = self.tickets.create(actor, safe_arguments, 'assistant:' + str(identifier))
                result = {'kind': 'ticket', 'ticket_id': ticket['ticket_id'], 'title': ticket['title'], 'status': ticket['status'],
                    'detail_url': '/api/v0.1/tickets/' + ticket['ticket_id'], 'deep_link': '#/tickets?ticket=' + ticket['ticket_id']}
            elif operation in ENGINEERING_OPERATIONS:
                result = self.engineering.perform(actor, session_id, turn_id, ordinal, operation, safe_arguments)
                if operation in {'diagnostic.run', 'api.read'}:
                    result = {'kind': 'data', 'operation': operation, 'data': result}
            elif operation == 'document.create':
                result = self.documents.create(actor, safe_arguments, 'assistant:' + str(identifier))
            elif operation == 'conversation.read':
                result = {'kind': 'data', 'operation': operation, 'data': conversation.read(safe_arguments)}
            else:
                result = {'kind': 'data', 'operation': operation, 'data': self.reader.read(operation, arguments)}
            result = self.clean(result)
        except Exception as error:
            uncertain = operation in AUTHORIZED_OPERATIONS and (not isinstance(error, CoreError) or error.code in {'TRANSIENT_DEPENDENCY_ERROR', 'INSPECTION_UNAVAILABLE'})
            code = 'ACTION_OUTCOME_UNKNOWN' if uncertain else error.code if isinstance(error, CoreError) else 'INSPECTION_UNAVAILABLE'
            message = ('Исход действия неизвестен; проверьте сохранённые результаты перед новой командой.' if uncertain
                else error.message if isinstance(error, CoreError) else 'Источник не прочитан; операция недоступна.')
            self.receipts.finish(actor, identifier, error={'code': code, 'message': message}, unknown=uncertain)
            raise CoreError(code, message) from None
        self.receipts.finish(actor, identifier, result=result)
        return result
