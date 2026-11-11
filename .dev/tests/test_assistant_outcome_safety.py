"""A tool's terminal receipt must distinguish known rejection from an uncertain effect."""
from uuid import uuid4

import pytest

from bervel.core.errors import CoreError
from test_assistant_workflows import actor_with_chat, build_actions


def document(filename='report.pdf'):
    return {'format': 'pdf', 'filename': filename, 'title': 'Отчёт', 'content': 'Проверенные данные: 11 строк.'}


def test_created_artifact_then_metadata_failure_is_unknown_and_cannot_create_a_second_file(services, pg_engine, monkeypatch):
    actions, actor, turn_id = build_actions(services, pg_engine), actor_with_chat(services), uuid4()
    def interrupted_metadata(*_):
        raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Private upstream diagnostic must not escape')
    monkeypatch.setattr(services[0], 'metadata', interrupted_metadata)
    with pytest.raises(CoreError) as failure:
        actions.execute(actor, turn_id, 0, 'file.create', document())
    assert failure.value.code == 'ACTION_OUTCOME_UNKNOWN'
    receipt = actions.receipts.read(actor, turn_id)[0]
    assert receipt['state'] == 'unknown'
    assert 'Private' not in receipt['error']['message']
    assert len(services[0].list(actor)['artifacts']) == 1
    with pytest.raises(CoreError) as replay:
        actions.execute(actor, turn_id, 0, 'file.create', document())
    assert replay.value.code == 'ACTION_OUTCOME_UNKNOWN'
    assert len(services[0].list(actor)['artifacts']) == 1


def test_known_filename_rejection_can_be_corrected_without_replaying_an_effect(services, pg_engine):
    actions, actor, turn_id = build_actions(services, pg_engine), actor_with_chat(services), uuid4()
    with pytest.raises(CoreError) as rejected:
        actions.execute(actor, turn_id, 0, 'file.create', document('../report.pdf'))
    assert rejected.value.code == 'VALIDATION_ERROR'
    assert actions.receipts.read(actor, turn_id)[0]['state'] == 'failed'
    assert services[0].list(actor)['artifacts'] == []
    created = actions.execute(actor, turn_id, 1, 'file.create', document())
    assert created['kind'] == 'file'
    assert len(services[0].list(actor)['artifacts']) == 1
