from copy import deepcopy
import json
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable

from .errors import CoreError
from .models import DataRecord


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


class SchemaRegistry:
    def __init__(self):
        self._schemas: dict[tuple[str, str], dict[str, Any]] = {}

    def register(self, definitions: dict[tuple[str, str], dict[str, Any]]) -> None:
        proposed = {**self._schemas, **deepcopy(definitions)}
        ids = {schema['$id']: schema for schema in proposed.values()}
        if len(ids) != len(proposed):
            raise CoreError('UNSUPPORTED_SCHEMA', 'Schema URI must be unique across registered versions')
        for key, schema in definitions.items():
            if key in self._schemas and canonical_json(schema) != canonical_json(self._schemas[key]):
                raise CoreError('UNSUPPORTED_SCHEMA', 'Schema version is immutable')
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as error:
                raise CoreError('UNSUPPORTED_SCHEMA', 'Invalid Draft 2020-12 schema') from error
            self._check_refs(schema, ids, schema['$id'])
        self._schemas = proposed

    def _check_refs(self, value: Any, ids: dict[str, Any], base: str) -> None:
        if isinstance(value, dict):
            for name, item in value.items():
                if name == '$ref':
                    target = item.split('#', 1)[0] or base
                    if target not in ids:
                        raise CoreError('UNSUPPORTED_SCHEMA', 'Unregistered schema reference')
                else:
                    self._check_refs(item, ids, base)
        elif isinstance(value, list):
            for item in value:
                self._check_refs(item, ids, base)

    def definitions(self) -> dict[tuple[str, str], dict[str, Any]]:
        return deepcopy(self._schemas)

    def validate_records(self, schema_id: str, version: str, records: list[DataRecord]) -> None:
        schema = self._schemas.get((schema_id, version))
        if schema is None:
            raise CoreError('UNSUPPORTED_SCHEMA', 'Schema version is not registered')
        local = Registry().with_resources((s['$id'], Resource.from_contents(s)) for s in self._schemas.values())
        validator = Draft202012Validator(schema, registry=local, format_checker=FormatChecker())
        for record in records:
            try:
                validator.validate(record.payload)
            except (ValidationError, Unresolvable) as error:
                raise CoreError('VALIDATION_ERROR', 'Record does not match its registered schema') from error
