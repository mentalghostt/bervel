from importlib.resources import files
import json

from bervel.config import N8nSettings
from test_n8n_adapter import server


def test_runtime_workflow_is_owned_by_module_and_renders_without_secrets():
    from bervel.infrastructure.n8n_setup import build_workflow
    template = files('bervel.modules.document_review').joinpath('workflows/extract.json')
    assert template.is_file()
    workflow = build_workflow('http://api:8011', 'bervel-test', 'callback-ref', 'webhook-ref')
    assert workflow['nodes'][0]['name'] == 'Start'
    assert workflow['nodes'][0]['parameters']['authentication'] == 'headerAuth'
    assert {node['type'] for node in workflow['nodes']} == {'n8n-nodes-base.webhook', 'n8n-nodes-base.httpRequest'}
    assert '/api/internal/v0.1/processes/' in workflow['nodes'][1]['parameters']['url']
    assert '$execution.id' in workflow['nodes'][-1]['parameters']['jsonBody']
    assert '.dev' not in json.dumps(workflow)
    assert '__' not in json.dumps(workflow)


def test_provision_uses_public_api_and_reuses_existing_workflow(server):
    from bervel.infrastructure.n8n_setup import provision_n8n
    url, handler = server
    handler.responses[('GET', '/api/v1/workflows?name=BERVEL+demo.document-review.parse+v1.0.0&limit=100')] = (200, {'data': [], 'nextCursor': None})
    handler.responses[('POST', '/api/v1/credentials')] = (200, {'id': 'credential-ref'})
    handler.responses[('POST', '/api/v1/workflows')] = (200, {'id': 'new-workflow', 'versionId': 'v1'})
    handler.responses[('POST', '/api/v1/workflows/new-workflow/publish')] = (200, {'id': 'new-workflow', 'activeVersionId': 'v1'})
    config = N8nSettings(url, 'a' * 32, 'h' * 32)
    result = provision_n8n(config, 'w' * 32)
    assert result['workflow_id'] == 'new-workflow'
    assert all('/rest/' not in call[1] for call in handler.calls)
    credential_values = [call[3]['data']['value'] for call in handler.calls if call[1] == '/api/v1/credentials']
    assert set(credential_values) == {'Bearer ' + 'w' * 32, 'Bearer ' + 'h' * 32}
    handler.calls.clear()
    handler.responses[('GET', '/api/v1/workflows?name=BERVEL+demo.document-review.parse+v1.0.0&limit=100')] = (200, {
        'data': [{'id': 'new-workflow', 'name': 'BERVEL demo.document-review.parse v1.0.0'}], 'nextCursor': None})
    handler.responses[('GET', '/api/v1/workflows/new-workflow')] = (200, {'id': 'new-workflow', 'active': True, 'activeVersionId': 'v1'})
    assert provision_n8n(config, 'w' * 32)['workflow_id'] == 'new-workflow'
    assert all(call[0] == 'GET' for call in handler.calls)
