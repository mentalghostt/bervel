"""HTTP validation and authorization for fixed radar profiles."""
import pytest
from fastapi.testclient import TestClient
from bervel.core.api.app import create_platform_app
from bervel.core.config import Settings
from bervel.core.models import ActorContext
from bervel.modules.ai_tender_radar.profile_api import profiles_router
from test_radar_profiles_storage import profile_scenario, run_scenario

API = '/api/v0.1/ai-tender-radar/profiles'


@pytest.fixture
def client(profile_scenario, tmp_path):
    s = profile_scenario
    keys = {'a'*32:s.actor,
            'c'*32:ActorContext('viewer',s.actor.data_scope,s.actor.permissions-{'process:start'}),
            'd'*32:ActorContext('no-artifact',s.actor.data_scope,s.actor.permissions-{'artifact:read'})}
    app, dependency = create_platform_app(Settings('unused', tmp_path/'profile-objects', keys), s.engine, s.artifacts)
    app.include_router(profiles_router(s.profiles, dependency))
    with TestClient(app) as http:
        http.headers['Authorization'] = 'Bearer '+'a'*32
        yield http


def test_two_profiles_and_zero_refresh(client):
    response = client.get(API)
    assert response.status_code == 200, response.text
    assert len(response.json()['profiles']) == 2
    response = client.post(API+'/test_api/refresh', json={'command_key':'http-zero','detail_max':0})
    assert response.status_code == 202, response.text
    first = response.json()
    assert set(first) == {'job_id','run_id','state'}
    assert client.post(API+'/test_api/refresh', json={'command_key':'http-zero','detail_max':0}).json() == first


@pytest.mark.parametrize('extra', [{'actor_id':'other'}, {'data_scope':'other'}, {'detail_max':True}, {'detail_max':-1}, {'detail_max':1.2}, {'schedule':{'enabled':True,'every_days':0,'time':'09:00','timezone':'Europe/Moscow'}}])
def test_refresh_rejects_invalid_input(client, extra):
    response = client.post(API+'/test_api/refresh', json={'command_key':'bad', 'detail_max':None, **extra})
    assert response.status_code == 422, response.text


def test_source_and_permissions(client):
    assert client.get(API+'/file').status_code == 422
    client.headers['Authorization'] = 'Bearer '+'c'*32
    assert client.post(API+'/test_api/refresh',json={'command_key':'denied','detail_max':0}).status_code == 403
    client.headers['Authorization'] = 'Bearer '+'d'*32
    assert client.post(API+'/test_api/refresh',json={'command_key':'denied','detail_max':0}).status_code == 403


def test_keywords_do_not_change_company_settings(client, profile_scenario):
    s = profile_scenario
    before = s.company.read(s.actor)
    response = client.put(API+'/test_api/keywords',json={'keyword_mode':'manual','keywords':['Болт','Болт']})
    assert response.status_code == 200,response.text
    assert response.json()['keywords'] == ['Болт']
    assert s.company.read(s.actor) == before
