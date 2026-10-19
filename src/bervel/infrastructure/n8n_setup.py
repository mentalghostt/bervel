from importlib.resources import files
import json
from urllib.parse import urlencode

from bervel.core.errors import CoreError
from .n8n import N8nEngine

WORKFLOW_NAME = 'BERVEL demo.document-review.parse v1.0.0'


def build_workflow(callback_url, webhook_path, callback_credential, webhook_credential):
    template = json.loads(files('bervel.modules.document_review').joinpath('workflows/extract.json').read_text(encoding='utf-8'))
    replacements = {'__CALLBACK_URL__': callback_url.rstrip('/'), '__WEBHOOK_PATH__': webhook_path,
                    '__CALLBACK_CREDENTIAL_ID__': callback_credential, '__WEBHOOK_CREDENTIAL_ID__': webhook_credential}
    def render(value):
        if isinstance(value, str):
            for source, target in replacements.items():
                value = value.replace(source, target)
            return value
        if isinstance(value, list):
            return [render(item) for item in value]
        if isinstance(value, dict):
            return {key: render(item) for key, item in value.items()}
        return value
    return render(template)


def provision_n8n(config, worker_key):
    engine = N8nEngine(config.base_url, config.api_key, config.webhook_key,
                       config.workflow_id or 'unprovisioned', config.webhook_path, config.timeout)
    workflow_id = config.workflow_id
    if not workflow_id:
        page = engine.request('GET', '/api/v1/workflows?' + urlencode({'name': WORKFLOW_NAME, 'limit': 100}))
        matches = [row for row in page.get('data', []) if row.get('name') == WORKFLOW_NAME]
        if len(matches) > 1 or page.get('nextCursor'):
            raise CoreError('WORKFLOW_MISMATCH', 'Select the workflow explicitly; deployment identity is ambiguous')
        workflow_id = matches[0]['id'] if matches else None
    if workflow_id:
        engine.workflow_id = workflow_id
        workflow = engine.request('GET', '/api/v1/workflows/' + workflow_id)
        if workflow.get('active') and workflow.get('activeVersionId'):
            return {'workflow_id': workflow_id, 'version_id': workflow['activeVersionId'], 'webhook_path': config.webhook_path}
        version_id = workflow['versionId']
    else:
        callback = engine.request('POST', '/api/v1/credentials', {'name': 'BERVEL process callback', 'type': 'httpHeaderAuth',
            'data': {'name': 'Authorization', 'value': 'Bearer ' + worker_key}})
        webhook = engine.request('POST', '/api/v1/credentials', {'name': 'BERVEL process start', 'type': 'httpHeaderAuth',
            'data': {'name': 'Authorization', 'value': 'Bearer ' + config.webhook_key}})
        workflow = engine.request('POST', '/api/v1/workflows',
            build_workflow(config.callback_url, config.webhook_path, callback['id'], webhook['id']))
        workflow_id, version_id = workflow['id'], workflow['versionId']
    published = engine.request('POST', '/api/v1/workflows/' + workflow_id + '/publish', {'versionId': version_id})
    return {'workflow_id': workflow_id, 'version_id': published.get('activeVersionId') or version_id,
            'webhook_path': config.webhook_path}
