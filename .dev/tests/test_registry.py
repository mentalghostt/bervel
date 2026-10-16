from copy import deepcopy
import pytest


def test_registered_module_and_payload_validation():
    from bervel.core.registry import default_registry
    from bervel.core.models import DataRecord
    from bervel.core.errors import CoreError
    registry = default_registry()
    module = registry.get('demo.document-review', '1.0.0')
    assert module.manifest['core_api_compatibility']['min_inclusive'] == '0.1.0'
    invalid = DataRecord('row', 'COMPLETE', {'name': 'A', 'quantity': {'value': 'unknown'}}, [], [])
    with pytest.raises(CoreError, match='VALIDATION_ERROR'):
        registry.validate_records('demo.document-review.requirement-row', '1.0.0', [invalid])
    with pytest.raises(CoreError, match='UNSUPPORTED_SCHEMA'):
        registry.validate_records('missing', '1.0.0', [])


def test_schema_versions_and_manifest_are_immutable():
    from bervel.core.registry import default_registry
    from bervel.core.errors import CoreError
    registry = default_registry()
    module = registry.get('demo.document-review', '1.0.0')
    manifest = deepcopy(module.manifest)
    schemas = deepcopy(module.schemas)
    registry.register(manifest, schemas, module.parser)
    schemas['demo.document-review.requirement-row']['properties']['name']['minLength'] = 2
    with pytest.raises(CoreError):
        registry.register(manifest, schemas, module.parser)
    manifest['core_api_compatibility']['min_inclusive'] = '0.2.0'
    with pytest.raises(CoreError, match='INCOMPATIBLE_MODULE'):
        registry.register(manifest, module.schemas, module.parser)
    module.schemas['demo.document-review.requirement-row']['properties']['name']['minLength'] = 999
    assert registry.get('demo.document-review', '1.0.0').schemas['demo.document-review.requirement-row']['properties']['name']['minLength'] == 1


def test_unregistered_references_do_not_fetch_the_network():
    from bervel.core.registry import default_registry
    from bervel.core.errors import CoreError
    registry = default_registry()
    module = registry.get('demo.document-review', '1.0.0')
    schema = deepcopy(module.schemas)
    schema['demo.document-review.requirement-row']['properties']['name'] = {'$ref': 'https://example.invalid/schema'}
    with pytest.raises(CoreError, match='UNSUPPORTED_SCHEMA'):
        registry.register(module.manifest, schema, module.parser)


def test_duplicate_schema_uri_cannot_change_existing_validation():
    from bervel.core.registry import default_registry
    from bervel.core.models import DataRecord
    from bervel.core.errors import CoreError
    registry = default_registry()
    common = registry.schemas.definitions()[('bervel.core.common', '0.1.0')]
    common['$defs']['quantity'] = {}
    with pytest.raises(CoreError, match='UNSUPPORTED_SCHEMA'):
        registry.schemas.register({('another.module.common', '1.0.0'): common})
    invalid = DataRecord('row', 'COMPLETE', {'name': 'A', 'quantity': {
        'state': 'KNOWN', 'value': 'not-a-number', 'unit': 'piece', 'original': {'value': '1', 'unit': 'pcs'}}}, [], [])
    with pytest.raises(CoreError, match='VALIDATION_ERROR'):
        registry.validate_records('demo.document-review.requirement-row', '1.0.0', [invalid])
