from copy import deepcopy
from uuid import UUID, uuid4

import pytest

from bervel.core.errors import CoreError
from bervel.core.models import RegisterArtifact
from test_presales_storage import actor, settings


class Company:
    def __init__(self):
        self.snapshot = {'id': str(uuid4()), 'profile': {'products': [], 'production': [], 'resources': []},
                         'stock': {'data_kind': 'reported', 'rows': []}}

    def read_analysis_snapshot(self, actor):
        return deepcopy(self.snapshot)


def service(services, pg_engine):
    from bervel.modules.presales.service import PresalesService
    from bervel.modules.presales.infrastructure.repository import PostgresPresalesRepository
    artifacts = services[0]
    return PresalesService(PostgresPresalesRepository(pg_engine), artifacts, Company())


def test_create_requires_saved_settings_and_each_selected_original_is_authorized(services, pg_engine):
    target = service(services, pg_engine)
    current_actor = actor(services[3].data_scope)
    ref = services[0].register(current_actor, RegisterArtifact(b'name;quantity\nBolt;2', 'request.csv',
        'text/csv', {'origin_system': 'client'}, 'client-file'))
    with pytest.raises(CoreError) as caught:
        target.create_request(current_actor, [str(ref.artifact_id)], 'new-request')
    assert caught.value.code == 'SETTINGS_REQUIRED'
    assert target.read_settings(current_actor)['settings'] == settings()
    target.save_settings(current_actor, None, settings(), 'save')
    first = target.create_request(current_actor, [str(ref.artifact_id)], 'new-request')
    assert target.create_request(current_actor, [str(ref.artifact_id)], 'new-request') == first
    assert target.read_request(current_actor, UUID(first['request_id']))['job']['state'] == 'queued'
    with pytest.raises(CoreError):
        target.create_request(actor('other-scope'), [str(ref.artifact_id)], 'other')


def test_read_revision_is_paginated_and_get_does_not_start_work(services, pg_engine):
    target = service(services, pg_engine)
    current_actor = actor(services[3].data_scope)
    ref = services[0].register(current_actor, RegisterArtifact(b'client', 'request.txt', 'text/plain', {}, 'client'))
    target.save_settings(current_actor, None, settings(), 'save')
    receipt = target.create_request(current_actor, [str(ref.artifact_id)], 'start')
    result = target.read_revision(current_actor, UUID(receipt['request_id']), UUID(receipt['revision_id']))
    assert result['metrics'] is None
    assert 'inputs' not in result
    assert 'stock' not in result
    assert target.list_lines(current_actor, UUID(receipt['request_id']), UUID(receipt['revision_id'])) == {'items': [], 'next_cursor': None}
    assert target.read_request(current_actor, UUID(receipt['request_id']))['job']['state'] == 'queued'


@pytest.mark.parametrize('value', [[], {}, 1, True, None])
def test_primary_list_choice_is_a_string_not_an_unhashable_http_value(value):
    from bervel.modules.presales.service import PresalesService
    with pytest.raises(CoreError) as caught:
        PresalesService._merge_decisions({}, {'primary_list_id': value}, {'lists': []}, actor())
    assert caught.value.code == 'VALIDATION_ERROR'


def test_explicit_null_revokes_an_approval_but_omission_preserves_other_lines():
    from bervel.modules.presales.service import PresalesService
    old = {'approvals': {'a': {'sku': 'A', 'analog_approved': True}, 'b': {'sku': 'B', 'analog_approved': True}}}
    extracted = {'lines': [{'line_id': 'a'}, {'line_id': 'b'}], 'lists': []}
    result = PresalesService._merge_decisions(old, {'approvals': {'a': None}}, extracted, actor())
    assert result['approvals'] == {'b': old['approvals']['b']}
    assert old['approvals']['a']['analog_approved']


def test_source_lists_are_available_paged_before_a_primary_list_is_selected(services, pg_engine):
    target = service(services, pg_engine)
    manager = actor(services[3].data_scope)
    ref = target.artifacts.register(manager, RegisterArtifact(b'client', 'request.txt', 'text/plain', {}, 'client'))
    target.save_settings(manager, None, settings(), 'settings')
    receipt = target.create_request(manager, [str(ref.artifact_id)], 'request')
    owned = target.repository.claim()
    extracted = {'title': 'Conflict', 'client': None, 'lines': [], 'primary_list_id': None,
        'lists': [{'list_id': 'a', 'filename': 'A.csv', 'complete': True, 'lines': [
            {'line_id': 'a1', 'name': 'Bolt', 'quantity': '10', 'unit': 'шт', 'requirements': [{'name': 'material', 'value': 'steel'}], 'evidence': []}]},
            {'list_id': 'b', 'filename': 'B.csv', 'complete': True, 'lines': [
            {'line_id': 'b1', 'name': 'Bolt', 'quantity': '20', 'unit': 'шт', 'requirements': [{'name': 'material', 'value': 'brass'}], 'evidence': []},
            {'line_id': 'b2', 'name': 'Nut', 'quantity': '2', 'unit': 'шт', 'requirements': [], 'evidence': []}]}]}
    target.repository.set_phase(owned, 'match', extracted=extracted)
    rid, revision_id = UUID(receipt['request_id']), UUID(receipt['revision_id'])
    first = target.list_lines(manager, rid, revision_id, cursor=0, limit=1, list_id='b')
    assert first['items'][0]['quantity'] == '20' and first['next_cursor'] == 1
    second = target.list_lines(manager, rid, revision_id, cursor=first['next_cursor'], limit=1, list_id='b')
    assert second['items'][0]['name'] == 'Nut' and second['next_cursor'] is None
    assert target.list_lines(manager, rid, revision_id)['items'] == []
    with pytest.raises(CoreError):
        target.list_lines(actor('different-scope'), rid, revision_id, list_id='b')
