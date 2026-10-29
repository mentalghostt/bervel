"""Operator HTTP and assistant commands must enqueue the same full radar pipeline."""
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from bervel.core.api.app import create_platform_app
from bervel.core.config import Settings
from bervel.modules.tender_radar.api import RunResponse, radar_router
from http_client import ASGITestClient
from test_assistant_workflows import build_actions
from test_radar import radar
from test_radar_upgrade import full_setup


@pytest.mark.parametrize('filters', [{}, {
    'min_price': '10.000', 'max_price': '1000.00', 'min_days_remaining': 3,
    'focus': 'болты', 'max_shortage_percent': '7.50', 'min_margin_percent': '15.00',
}])
def test_operator_and_assistant_enqueue_identical_pipeline_inputs(radar, services, pg_engine, monkeypatch, filters):
    service, actor, _, refs = full_setup(radar, monkeypatch)
    actor = replace(actor, permissions=actor.permissions | {'chat:read', 'chat:write'})
    settings = Settings(pg_engine.url.render_as_string(hide_password=False), services[2].root, {'a'*32: actor})
    app, dependency = create_platform_app(settings, pg_engine, services[0])
    app.include_router(radar_router(service, dependency))
    reference = lambda ref: {'artifact_id': str(ref.artifact_id)}
    args = dict(zip(('procurement', 'stock', 'economics'), map(reference, refs))) | {'filters': filters}

    with ASGITestClient(app) as client:
        response = client.post('/api/v0.1/radar/analyse', headers={'Authorization': 'Bearer '+'a'*32}, json={
            'command_key': 'operator-'+uuid4().hex,
            **{key+'_source': value for key, value in args.items() if key!='filters'}, 'filters': filters,
        })
    assert response.status_code == 202
    operator = response.json()

    actions = build_actions(services, pg_engine, radar=service)
    card = actions.execute(actor, uuid4(), 0, 'radar.analyse', args)
    assistant = RunResponse.model_validate(service.read(actor, UUID(card['run_id']))).model_dump(mode='json')

    assert operator['run_id'] != assistant['run_id']
    assert operator['selection'] == assistant['selection']
    assert operator['sources_snapshot'] == assistant['sources_snapshot']
    assert operator['focus'] == assistant['focus'] == filters.get('focus', '')
    assert operator['selection']['pipeline_version'] == assistant['selection']['pipeline_version'] == '1.3'
    assert operator['state'] == assistant['state'] == 'queued'
    assert card['deep_link'].endswith('/#/radar?run='+assistant['run_id'])
    assert len(radar[4].calls) == 0  # Queueing itself never runs an alternate model algorithm.
