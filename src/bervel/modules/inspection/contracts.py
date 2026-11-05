"""One closed contract shared by the model validator and the diagnostic reader."""
from bervel.core.errors import CoreError
from bervel.core.model_contract import obj, string, strict_wire_schema, validate

TICKET = obj({'title': string(200, minLength=1), 'problem': string(8000, minLength=1), 'expected': string(),
    'actual': string(), 'steps': {'type': 'array', 'maxItems': 50, 'items': string(4000, minLength=1)},
    'context': string(), 'links': {'type': 'array', 'maxItems': 30, 'items': string(2048)}})

PAGE = {'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 32000}}
FILE = {'root': string(40, enum=['project', 'objects', 'codex', 'embeddings', 'postgres']), 'path': string(4096, minLength=1), **PAGE}
CONTAINER = {'container': string(255, minLength=1)}
READ_OPERATIONS = {
    'conversation.read': obj({'offset': PAGE['offset'], 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20},
        'query': string(1000)}, []),
    'system.list': obj({**FILE, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200}}, []),
    'system.read': obj(FILE, ['path']),
    'database.tables': obj({}),
    'database.schema': obj({'table': string(128, minLength=1)}),
    'database.rows': obj({'table': string(128, minLength=1), 'columns': {'type': 'array', 'maxItems': 100, 'items': string(128)},
        'filters': {'type': 'array', 'maxItems': 10, 'items': obj({'column': string(128), 'value': string(4000)})},
        'offset': PAGE['offset'], 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, ['table']),
    'container.list': obj({}),
    'container.inspect': obj(CONTAINER),
    'container.logs': obj({**CONTAINER, 'since': string(40), 'until': string(40),
        'tail': {'type': 'integer', 'minimum': 1, 'maximum': 10000}, **PAGE}, ['container']),
    'container.files': obj({**CONTAINER, 'path': string(4096, minLength=1), **PAGE}, ['container', 'path']),
}
ENGINEERING_OPERATIONS = {
    'diagnostic.run': obj({'check': string(enum=['git.status', 'git.diff', 'python.syntax']), 'path': string(4096, minLength=1)}, ['check']),
    'api.read': obj({'path': string(4096, minLength=1)}),
    'test.run': obj({'suite': string(enum=['backend', 'frontend', 'runner']),
        'targets': {'type': 'array', 'maxItems': 20, 'items': string(4096, minLength=1)}}, ['suite']),
    'code.propose': obj({'title': string(200, minLength=1), 'explanation': string(4000, minLength=1),
        'changes': {'type': 'array', 'maxItems': 20, 'items': obj({'path': string(4096, minLength=1),
            'content': {'anyOf': [string(200000), {'type': 'null'}]}})}}),
}
DOCUMENT = obj({'format': string(enum=['pdf', 'docx', 'svg', 'txt', 'csv']),
    'filename': string(255, minLength=1), 'title': string(200, minLength=1), 'content': string(64000, minLength=1)})
AUTHORIZED_OPERATIONS = frozenset({'ticket.create', 'document.create', 'test.run', 'code.propose'})
OPERATIONS = {**READ_OPERATIONS, **ENGINEERING_OPERATIONS, 'ticket.create': TICKET, 'document.create': DOCUMENT}
PLAN = obj({'answer': string(16000), 'actions': {'type': 'array', 'maxItems': 4, 'items': {'anyOf': [
    obj({'operation': string(enum=[name]), 'arguments': arguments, 'authorization_quote': string(4000)})
    for name, arguments in OPERATIONS.items()]}}})
WIRE_PLAN = strict_wire_schema(PLAN)


def validate_operation(operation: str, arguments: dict, *, reads_only: bool = False) -> None:
    allowed = READ_OPERATIONS if reads_only else OPERATIONS
    if operation not in allowed:
        raise CoreError('FORBIDDEN', 'Операция не входит в разрешённый контракт «Проверки».')
    validate(arguments, allowed[operation])
