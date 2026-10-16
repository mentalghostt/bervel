from test_api import client, auth, upload, PREFIX


def test_artifact_history_cannot_bypass_dataset_read_permission(client):
    from bervel.core.models import ActorContext
    from bervel.api import create_app
    from bervel.config import Settings
    from http_client import ASGITestClient
    from uuid import uuid4
    http, keys, settings = client
    original = upload(http, keys[0]).json()
    http.post(PREFIX + '/demo/document-review/extractions', headers=auth(keys[0]), json={
        'artifact_id': original['artifact_id'], 'artifact_revision': 1, 'command_key': 'extract'})
    key = uuid4().hex
    limited = Settings(settings.database_url, settings.object_root,
        {key: ActorContext('limited', 'test-area-a', frozenset({'artifact:read'}))})
    with ASGITestClient(create_app(limited)) as only_originals:
        assert only_originals.get(PREFIX + f"/artifacts/{original['artifact_id']}/revisions/1", headers=auth(key)).status_code == 200
        response = only_originals.get(PREFIX + f"/artifacts/{original['artifact_id']}/history", headers=auth(key))
        assert response.status_code == 403
        assert 'process_run_id' not in response.text and 'dataset_id' not in response.text
