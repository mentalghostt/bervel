"""Original listing is persistent, scoped and independent of retired datasets."""
from uuid import uuid4
import pytest
from test_api import PREFIX, auth, client, upload


def test_listing_is_persistent_scoped_and_paginated(client):
    from bervel.app_setup import create_app
    from http_client import ASGITestClient
    http, keys, settings = client
    assert http.get(PREFIX + '/artifacts', headers=auth(keys[0])).json() == {'artifacts': [], 'next_cursor': None}
    first=upload(http, keys[0]).json()
    second=upload(http, keys[0], command_key='second').json()
    foreign=upload(http, keys[1]).json()
    page=http.get(PREFIX + '/artifacts?limit=1', headers=auth(keys[2])).json()
    assert page['next_cursor']==1 and page['artifacts'][0]['artifact_id']==second['artifact_id']
    next_page=http.get(PREFIX + '/artifacts?limit=1&cursor=1', headers=auth(keys[2])).json()
    assert next_page['next_cursor'] is None and next_page['artifacts'][0]['artifact_id']==first['artifact_id']
    assert set(next_page['artifacts'][0])=={'artifact_id','filename','media_type','size','created_at',
        'usage_role','processing_state','completed_chunks','total_chunks','processing_error'}
    assert foreign['artifact_id'] not in str(page) and 'object_ref' not in str(page)
    with ASGITestClient(create_app(settings)) as reopened:
        actual=reopened.get(PREFIX+'/artifacts', headers=auth(keys[0])).json()
        assert {i['artifact_id'] for i in actual['artifacts']}=={first['artifact_id'],second['artifact_id']}


def test_another_upload_preserves_both_files_without_processing(client):
    http, keys, _=client
    first=upload(http,keys[0]).json()
    second=upload(http,keys[0],2).json()
    items=http.get(PREFIX+'/artifacts',headers=auth(keys[0])).json()['artifacts']
    assert len(items)==2 and {i['artifact_id'] for i in items}=={first['artifact_id'],second['artifact_id']}
    assert all('revision' not in item and 'dataset' not in item for item in items)


def test_listing_requires_both_read_permissions_and_does_not_write(client):
    from bervel.app_setup import create_app
    from bervel.core.config import Settings
    from bervel.core.models import ActorContext
    from http_client import ASGITestClient
    from sqlalchemy import create_engine, text
    http, keys, settings = client
    upload(http, keys[0])
    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        before = connection.execute(text('SELECT count(*) FROM audit')).scalar_one()
    for permission in ('artifact:read', 'dataset:read'):
        key = uuid4().hex
        limited = Settings(settings.database_url, settings.object_root,
                           {key: ActorContext('limited', 'test-area-a', frozenset({permission}))})
        with ASGITestClient(create_app(limited)) as limited_http:
            denied = limited_http.get(PREFIX + '/artifacts', headers=auth(key))
            assert denied.status_code == 403
            assert 'filename' not in denied.text and 'dataset_id' not in denied.text
    assert http.get(PREFIX + '/artifacts').status_code == 401
    assert http.get(PREFIX + '/artifacts', headers=auth('unknown')).status_code == 401
    assert http.get(PREFIX + '/artifacts', headers=auth(keys[2])).status_code == 200
    with engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM audit')).scalar_one() == before
    engine.dispose()


@pytest.mark.parametrize('query', ['cursor=-1', 'limit=0', 'limit=101', 'cursor=abc'])
def test_listing_validates_pagination(client, query):
    http, keys, settings = client
    assert http.get(PREFIX + '/artifacts?' + query, headers=auth(keys[0])).status_code == 422
