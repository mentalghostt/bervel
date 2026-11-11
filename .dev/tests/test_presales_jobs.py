from copy import deepcopy
import json
from uuid import UUID

import pytest

from bervel.core.errors import CoreError
from bervel.core.models import RegisterArtifact
from test_presales_analysis import fixture
from test_presales_service import service
from test_presales_storage import actor, settings


class Reader:
    def __init__(self): self.calls = 0
    def read(self, artifact):
        self.calls += 1
        return [dict(section_id='row-1', artifact_id=str(artifact.ref.artifact_id), filename='request.txt',
                     text='ITEM:Болт М12:2:steel', unit='row', location={'row': 1}, complete=True, issues=[])]


class Model:
    def __init__(self): self.calls = []; self.after_extract = None
    def generate(self, request_id, prompt, **kwargs):
        self.calls.append(request_id)
        if '\nSOURCE_DATA_JSON\n' in prompt:
            sections = json.loads(prompt.split('\nSOURCE_DATA_JSON\n')[1])
            answer = dict(title='Заявка клиента', client='Клиент', requested_deadline=None, client_terms={},
                lines=[dict(name='Болт М12', quantity='2', unit='шт', requirements=[],
                            section_ids=[sections[0]['section_id']], client_unit_price=None, currency=None)],
                complete=True, warnings=[])
            if self.after_extract: self.after_extract()
        else:
            answer = dict(text='Достаточно склада.', issues=[], score=80)
        return {'answer': json.dumps(answer, ensure_ascii=False), 'model': 'test-model'}


class Renderer:
    def __init__(self): self.pdf_calls = 0; self.docx_calls = 0; self.fail_docx = False
    def render_pdf(self, offer):
        self.pdf_calls += 1
        return b'PDF:' + json.dumps(offer, sort_keys=True).encode()
    def render_docx(self, offer):
        self.docx_calls += 1
        if self.fail_docx:
            self.fail_docx = False
            raise CoreError('DOCUMENT_RENDER_FAILED', 'Retry document renderer')
        return b'DOCX:' + json.dumps(offer, sort_keys=True).encode()


def prepared(services, pg_engine):
    target = service(services, pg_engine)
    current_actor = actor(services[3].data_scope)
    _, snapshot = fixture()
    target.company.snapshot = deepcopy(snapshot)
    target.reader, target.model, target.renderer = Reader(), Model(), Renderer()
    ref = target.artifacts.register(current_actor, RegisterArtifact(b'client file', 'request.txt', 'text/plain', {}, 'source'))
    target.save_settings(current_actor, None, settings(), 'settings')
    receipt = target.create_request(current_actor, [str(ref.artifact_id)], 'request')
    return target, current_actor, receipt


def test_full_pipeline_exports_once_and_keeps_internal_economics_outside_offer(services, pg_engine):
    target, manager, receipt = prepared(services, pg_engine)
    assert target.run_once()
    revision = target.read_revision(manager, UUID(receipt['request_id']), UUID(receipt['revision_id']))
    assert revision['state'] == 'complete'
    assert revision['score'] == 80
    assert revision['offer']['totals'] == {'RUB': '400.00'}
    assert revision['artifacts']['pdf'] and revision['artifacts']['docx']
    assert 'unit_cost' not in json.dumps(revision['offer'])
    assert not target.run_once()
    assert (target.reader.calls, target.renderer.pdf_calls, target.renderer.docx_calls) == (1, 1, 1)


def test_stop_after_model_result_keeps_paid_result_and_resume_does_not_reextract(services, pg_engine):
    target, manager, receipt = prepared(services, pg_engine)
    target.model.after_extract = lambda: target.stop_job(manager, UUID(receipt['request_id']), UUID(receipt['job_id']), 'stop')
    assert target.run_once()
    assert target.read_request(manager, UUID(receipt['request_id']))['job']['state'] == 'stopped'
    assert len(target.model.calls) == 1
    target.model.after_extract = None
    target.resume_job(manager, UUID(receipt['request_id']), UUID(receipt['job_id']), 'resume')
    assert target.run_once()
    assert target.read_request(manager, UUID(receipt['request_id']))['job']['state'] == 'complete'
    assert len(target.model.calls) == 2  # extraction reused; only the review is a new model call
    assert target.reader.calls == 1


def test_export_failure_resumes_without_duplicating_pdf_or_model_calls(services, pg_engine):
    target, manager, receipt = prepared(services, pg_engine)
    target.renderer.fail_docx = True
    target.run_once()
    partial = target.read_revision(manager, UUID(receipt['request_id']), UUID(receipt['revision_id']))
    assert partial['state'] == 'failed' and partial['artifacts']['pdf']
    paid_calls = len(target.model.calls)
    target.resume_job(manager, UUID(receipt['request_id']), UUID(receipt['job_id']), 'resume')
    target.run_once()
    final = target.read_revision(manager, UUID(receipt['request_id']), UUID(receipt['revision_id']))
    assert final['state'] == 'complete'
    assert final['artifacts']['pdf'] == partial['artifacts']['pdf']
    assert target.renderer.pdf_calls == 1
    assert len(target.model.calls) == paid_calls


def test_manager_update_merges_previous_decisions_and_creates_new_immutable_result(services, pg_engine):
    target, manager, receipt = prepared(services, pg_engine)
    target.run_once()
    request_id, first_id = UUID(receipt['request_id']), UUID(receipt['revision_id'])
    line_id = target.list_lines(manager, request_id, first_id)['items'][0]['line_id']
    old = target.read_revision(manager, request_id, first_id)
    second = target.create_revision(manager, request_id, first_id, 'manager_update',
        {'line_updates': {line_id: {'quantity': '3'}}}, 'second')
    target.run_once()
    third = target.create_revision(manager, request_id, UUID(second['revision_id']), 'manager_update',
                                    {'client': 'Новый клиент'}, 'third')
    target.run_once()
    result = target.read_revision(manager, request_id, UUID(third['revision_id']))
    assert result['offer']['client'] == 'Новый клиент'
    assert result['offer']['totals'] == {'RUB': '600.00'}
    assert result['decisions']['line_updates'][line_id]['quantity'] == '3'
    assert target.read_revision(manager, request_id, first_id) == old
    assert target.reader.calls == 1


def test_lost_export_checkpoint_recovers_registered_artifact_before_rendering_again(services, pg_engine, monkeypatch):
    target, manager, receipt = prepared(services, pg_engine)
    original = target.repository.set_phase
    failures = [True]
    def fail_after_registration(owned, phase, **result):
        if result.get('artifacts', {}).get('pdf') and failures:
            failures.pop()
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Lost checkpoint after committed registration')
        return original(owned, phase, **result)
    monkeypatch.setattr(target.repository, 'set_phase', fail_after_registration)
    def nondeterministic_pdf(offer):
        target.renderer.pdf_calls += 1
        return str(target.renderer.pdf_calls).encode()
    monkeypatch.setattr(target.renderer, 'render_pdf', nondeterministic_pdf)
    target.run_once()
    assert target.read_request(manager, UUID(receipt['request_id']))['job']['state'] == 'failed'
    target.resume_job(manager, UUID(receipt['request_id']), UUID(receipt['job_id']), 'resume-export')
    target.run_once()
    assert target.read_request(manager, UUID(receipt['request_id']))['job']['state'] == 'complete'
    assert target.renderer.pdf_calls == 1


def test_explicit_resume_can_replace_a_malformed_model_response(services, pg_engine, monkeypatch):
    target, manager, receipt = prepared(services, pg_engine)
    original = target.model.generate
    bad = [True]
    def malformed_once(*args, **kwargs):
        if bad:
            bad.pop()
            return {'answer': 'not JSON', 'model': 'test'}
        return original(*args, **kwargs)
    monkeypatch.setattr(target.model, 'generate', malformed_once)
    target.run_once()
    assert target.read_request(manager, UUID(receipt['request_id']))['job']['state'] == 'failed'
    target.resume_job(manager, UUID(receipt['request_id']), UUID(receipt['job_id']), 'resume-invalid')
    target.run_once()
    assert target.read_request(manager, UUID(receipt['request_id']))['job']['state'] == 'complete'
