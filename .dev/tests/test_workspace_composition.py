"""New public workflows share the existing app and authorization boundary."""
from test_api import client, auth, PREFIX


def test_workspace_routes_are_present_and_enforce_existing_actor(client):
    http,keys,_=client
    schema=http.get('/openapi.json').json()['paths']
    assert PREFIX+'/tickets' in schema
    assert PREFIX+'/voice/transcribe' in schema
    assert PREFIX+'/ai-tender-radar/jobs' in schema
    assert http.get(PREFIX+'/tickets',headers=auth(keys[0])).status_code==403
    assert http.get(PREFIX+'/voice/settings',headers=auth(keys[0])).status_code==403
