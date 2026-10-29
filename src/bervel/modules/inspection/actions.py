"""Inspection has no reference to file writers, settings, radar or Docker controls."""
from uuid import UUID
from bervel.core.errors import CoreError
from bervel.core.models import ActorContext
from bervel.core.services import authorize
from bervel.modules.inspection.contracts import validate_operation


class InspectionActions:
    def __init__(self, *, reader, receipts, tickets):
        self.reader, self.receipts, self.tickets = reader, receipts, tickets

    def clean(self, value):
        return self.reader.redact(value)

    def execute(self, actor: ActorContext, turn_id: UUID, ordinal: int, operation: str, arguments: dict,
                *, ticket_authorized: bool = False) -> dict:
        authorize(actor, 'chat:write')
        validate_operation(operation, arguments)
        if operation == 'ticket.create' and ticket_authorized is not True:
            raise CoreError('ACTION_NOT_AUTHORIZED', 'Создание обращения требует прямой просьбы пользователя.')
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
            else:
                result = {'kind': 'data', 'operation': operation, 'data': self.reader.read(operation, arguments)}
            result = self.clean(result)
        except Exception as error:
            uncertain = operation == 'ticket.create' and (not isinstance(error, CoreError) or error.code in {'TRANSIENT_DEPENDENCY_ERROR', 'INSPECTION_UNAVAILABLE'})
            code = 'ACTION_OUTCOME_UNKNOWN' if uncertain else error.code if isinstance(error, CoreError) else 'INSPECTION_UNAVAILABLE'
            message = 'Исход создания обращения неизвестен; проверьте «Обращения» перед новой командой.' if uncertain else 'Источник не прочитан или действие отклонено; изменения системы не выполнялись.'
            self.receipts.finish(actor, identifier, error={'code': code, 'message': message}, unknown=uncertain)
            raise CoreError(code, message) from None
        self.receipts.finish(actor, identifier, result=result)
        return result
