from copy import deepcopy
from dataclasses import dataclass
from importlib.resources import files
import json
import re
from typing import Any, Callable

from .errors import CoreError
from .models import ArtifactRevisionRef, ExtractionResult
from .validation import SchemaRegistry, canonical_json


def version_tuple(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value):
        raise CoreError('INCOMPATIBLE_MODULE', 'Invalid semantic version')
    return tuple(map(int, value.split('.')))


@dataclass(frozen=True)
class ModuleDefinition:
    manifest: dict[str, Any]
    schemas: dict[str, dict[str, Any]]
    parser: Callable[[bytes, ArtifactRevisionRef], ExtractionResult]


class ModuleRegistry:
    def __init__(self):
        self._modules: dict[tuple[str, str], ModuleDefinition] = {}
        self.schemas = SchemaRegistry()

    def register(self, manifest: dict[str, Any], schemas: dict[str, dict[str, Any]], parser: Callable) -> None:
        required = {'manifest_version', 'module_id', 'module_version', 'core_api_compatibility',
                    'dependencies', 'permissions', 'configuration_schema', 'workflow_types', 'data_types',
                    'event_types', 'owned_tables'}
        if not required.issubset(manifest) or manifest['manifest_version'] != '0.1.0':
            raise CoreError('INCOMPATIBLE_MODULE', 'Incomplete module manifest')
        for field in ('dependencies', 'permissions', 'workflow_types', 'data_types', 'event_types', 'owned_tables'):
            if not isinstance(manifest[field], list):
                raise CoreError('INCOMPATIBLE_MODULE', 'Invalid manifest list')
        compatibility = manifest['core_api_compatibility']
        if not version_tuple(compatibility['min_inclusive']) <= (0, 1, 0) < version_tuple(compatibility['max_exclusive']):
            raise CoreError('INCOMPATIBLE_MODULE', 'Module does not support core 0.1.0')
        key = (manifest['module_id'], manifest['module_version'])
        version_tuple(key[1])
        for dependency in manifest['dependencies']:
            candidates = [v for (name, v) in self._modules if name == dependency['module_id']]
            if not any(version_tuple(dependency['min_inclusive']) <= version_tuple(v) < version_tuple(dependency['max_exclusive']) for v in candidates):
                raise CoreError('INCOMPATIBLE_MODULE', 'Required dependency is unavailable')
        if key in self._modules and canonical_json(self._modules[key].manifest) != canonical_json(manifest):
            raise CoreError('INCOMPATIBLE_MODULE', 'Module version is immutable')
        definitions = {}
        for data_type in manifest['data_types']:
            schema_id = data_type['schema_id']
            if not schema_id.startswith(key[0] + '.') or schema_id not in schemas:
                raise CoreError('UNSUPPORTED_SCHEMA', 'Schema must belong to its module')
            definitions[(schema_id, data_type['schema_version'])] = schemas[schema_id]
        self.schemas.register(definitions)
        self._modules[key] = ModuleDefinition(deepcopy(manifest), deepcopy(schemas), parser)

    def get(self, module_id: str, version: str) -> ModuleDefinition:
        if (module_id, version) not in self._modules:
            raise CoreError('INCOMPATIBLE_MODULE', 'Module version is not registered')
        return deepcopy(self._modules[(module_id, version)])

    def list(self) -> list[dict[str, Any]]:
        return [deepcopy(m.manifest) for m in self._modules.values()]

    def validate_records(self, schema_id: str, version: str, records: list) -> None:
        self.schemas.validate_records(schema_id, version, records)


def default_registry() -> ModuleRegistry:
    from bervel.modules.document_review.module import parse_csv
    registry = ModuleRegistry()
    common = json.loads(files('bervel.core').joinpath('schemas/common.json').read_text(encoding='utf-8'))
    registry.schemas.register({('bervel.core.common', '0.1.0'): common})
    package = files('bervel.modules.document_review')
    manifest = json.loads(package.joinpath('manifest.json').read_text(encoding='utf-8'))
    schema = json.loads(package.joinpath('schemas/requirement-row.json').read_text(encoding='utf-8'))
    registry.register(manifest, {'demo.document-review.requirement-row': schema}, parse_csv)
    return registry
