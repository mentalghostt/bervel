from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from bervel.core.errors import CoreError
from test_presales_storage import actor


class SettingsService:
    def __init__(self): self.calls = []
    def read_settings(self, context):
        self.calls.append('read')
        return {'snapshot_id': None, 'settings': {'target_margin_percent': '50',
            'special_min_margin_percent': '70', 'seller_contacts': '', 'offer_terms': ''}, 'requires_save': True}
    def create_request(self, context, ids, key):
        self.calls.append(('create', ids, key))
        return {'request_id': 'a', 'revision_id': 'b', 'job_id': 'c', 'state': 'queued'}


def test_api_validates_unknown_fields_and_does_not_turn_get_into_start():
    from bervel.modules.presales.api import presales_router
    service = SettingsService()
    app = FastAPI()
    app.include_router(presales_router(service, lambda: actor()))
    with TestClient(app) as client:
        result = client.get('/api/v0.1/presales/settings')
        assert result.status_code == 200 and result.json()['settings']['target_margin_percent'] == '50'
        assert service.calls == ['read']
        assert client.post('/api/v0.1/presales/requests', json={'artifact_ids': ['a'], 'command_key': 'c', 'unknown': True}).status_code == 422
        assert client.post('/api/v0.1/presales/requests', json={'artifact_ids': ['a'], 'command_key': 'c'}).status_code == 202
