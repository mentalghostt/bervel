"""JSON schema helpers for server-validated model plans; no transport dependencies."""
from bervel.core.errors import CoreError
from uuid import UUID


def obj(properties: dict, required=None) -> dict:
    return {'type': 'object', 'properties': properties, 'required': list(properties) if required is None else required,
            'additionalProperties': False}


def string(maximum=8000, **values):
    return {'type': 'string', 'maxLength': maximum, **values}


def strict_wire_schema(schema):
    """Native structured output requires all object properties; null expresses runtime defaults."""
    if isinstance(schema, list): return [strict_wire_schema(item) for item in schema]
    if not isinstance(schema, dict): return schema
    result = {key: strict_wire_schema(value) for key, value in schema.items()}
    if schema.get('type') == 'object':
        optional = set(schema['properties']) - set(schema['required'])
        for name in optional:
            value = result['properties'][name]
            nullable = value.get('type') == 'null' or any(item.get('type') == 'null' for item in value.get('anyOf', []))
            if not nullable: result['properties'][name] = {'anyOf': [value, {'type': 'null'}]}
        result['required'] = list(schema['properties'])
    return result



def normalize_defaults(value, schema):
    if isinstance(value, dict) and schema.get('type') == 'object':
        return {name: normalize_defaults(item, schema['properties'].get(name, {})) for name, item in value.items()
            if not (item is None and name in schema['properties'] and name not in schema['required'])}
    return value


def invalid() -> None:
    raise CoreError('ASSISTANT_INVALID_PLAN', 'Модель вернула некорректное действие. Изменения не выполнены.')


def validate(value, schema: dict) -> None:
    if 'anyOf' in schema:
        for branch in schema['anyOf']:
            try: validate(value, branch); return
            except CoreError: pass
        invalid()
    kind = schema.get('type')
    if kind == 'object':
        if not isinstance(value, dict) or set(value) - set(schema['properties']) or set(schema['required']) - set(value): invalid()
        for name, item in value.items(): validate(item, schema['properties'][name])
    elif kind == 'array':
        if not isinstance(value, list) or len(value) > schema.get('maxItems', 100): invalid()
        for item in value: validate(item, schema['items'])
    elif kind == 'string':
        if not isinstance(value, str) or not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 8000): invalid()
        if schema.get('format') == 'uuid':
            try: UUID(value)
            except ValueError: invalid()
    elif kind == 'integer':
        if type(value) is not int or not schema.get('minimum', -10**12) <= value <= schema.get('maximum', 10**12): invalid()
    elif kind == 'boolean' and type(value) is not bool: invalid()
    elif kind == 'null' and value is not None: invalid()
    if 'enum' in schema and value not in schema['enum']: invalid()


