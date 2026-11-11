"""Removing the educational workflow must leave real document operations intact."""
from test_api import client, auth, PREFIX


def test_educational_routes_are_absent(client):
    http, keys, _ = client
    paths = http.get('/openapi.json').json()['paths']
    assert not any('/demo/' in p or '/datasets' in p or '/process' in p or p.endswith('/modules') for p in paths)
    for path in ('/demo/document-review/extractions', '/demo/document-review/process-runs'):
        assert http.post(PREFIX + path, headers=auth(keys[0]), json={}).status_code == 404


def test_arbitrary_csv_is_stored_and_previewed_without_a_dataset(client):
    http, keys, _ = client
    content = b'product;quantity\nRADAR_BOLT;17\n'
    response = http.post(PREFIX + '/artifacts', headers=auth(keys[0]),
                         data={'command_key': 'plain-csv'},
                         files={'file': ('profile.csv', content, 'text/csv')})
    assert response.status_code == 200
    ref = response.json()
    prefix = f"{PREFIX}/artifacts/{ref['artifact_id']}"
    assert http.get(prefix + '/content', headers=auth(keys[0])).content == content
    preview = http.get(prefix + '/preview', headers=auth(keys[0]))
    assert preview.status_code == 200
    assert preview.json()['rows'] == [['RADAR_BOLT', '17']]
    files = http.get(PREFIX + '/artifacts', headers=auth(keys[0])).json()['artifacts']
    assert files[0]['artifact_id'] == ref['artifact_id']
    assert 'dataset' not in files[0]


